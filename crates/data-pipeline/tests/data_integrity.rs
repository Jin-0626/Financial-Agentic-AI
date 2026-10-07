use chrono::{NaiveDate, Utc};
use data_pipeline::*;
use rust_decimal::Decimal;
fn snapshot(org: &str) -> Snapshot {
    let bars = [
        ("2025-01-01", 100.0),
        ("2025-01-02", 110.0),
        ("2025-01-03", 99.0),
    ]
    .into_iter()
    .map(|(date, close)| PriceBar {
        date: match NaiveDate::parse_from_str(date, "%Y-%m-%d") {
            Ok(date) => date,
            Err(_) => panic!("test date"),
        },
        adjusted_close: close,
        volume: Some(0),
    })
    .collect();
    Snapshot {
        schema_version: 1,
        org_id: org.into(),
        ticker: "0157.KL".into(),
        currency: "MYR".into(),
        unit_multiplier: Decimal::ONE,
        provider: "fixture".into(),
        source_reference: "fixture adjusted prices".into(),
        retrieved_at: Utc::now(),
        as_of: match NaiveDate::parse_from_str("2025-01-03", "%Y-%m-%d") {
            Ok(date) => date,
            Err(_) => panic!("test date"),
        },
        dataset: Dataset::Prices { bars },
    }
}
#[test]
fn parquet_lazy_returns_and_organization_isolation() -> Result<(), Box<dyn std::error::Error>> {
    let root = tempfile::tempdir()?;
    let store = SnapshotStore::new(root.path(), "org-a")?;
    let other = SnapshotStore::new(root.path(), "org-b")?;
    let id = store.import(snapshot("org-a"))?;
    let (_, returns, log, spot) = store.returns(&id, "0157.KL", 2)?;
    assert!((returns[0] - 0.1).abs() < 1e-12);
    assert!((returns[1] + 0.1).abs() < 1e-12);
    assert!((log[0] - 1.1_f64.ln()).abs() < 1e-12);
    assert_eq!(spot, 99.0);
    assert!(other.load(&id, "0157.KL", "prices").is_err());
    assert!(store.load(&id, "AAPL", "prices").is_err());
    assert!(store.load("../../private", "0157.KL", "prices").is_err());
    Ok(())
}
#[test]
fn integrity_failure_and_immutable_import() -> Result<(), Box<dyn std::error::Error>> {
    let root = tempfile::tempdir()?;
    let store = SnapshotStore::new(root.path(), "org-a")?;
    let input = snapshot("org-a");
    let id = store.import(input.clone())?;
    assert_eq!(id, store.import(input)?);
    let path = root
        .path()
        .join(digest(b"org-a"))
        .join(format!("{id}.parquet"));
    std::fs::write(path, b"corrupt")?;
    assert!(matches!(
        store.load(&id, "0157.KL", "prices"),
        Err(PipelineError::Corrupt)
    ));
    Ok(())
}
#[test]
fn duplicate_dates_nonfinite_prices_currency_and_units_fail() {
    let mut input = snapshot("org-a");
    if let Dataset::Prices { bars } = &mut input.dataset {
        bars[1].date = bars[0].date;
    }
    assert!(input.validate().is_err());
    input = snapshot("org-a");
    if let Dataset::Prices { bars } = &mut input.dataset {
        bars[0].adjusted_close = f64::NAN;
    }
    assert!(input.validate().is_err());
    input = snapshot("org-a");
    input.currency = "mixed".into();
    assert!(input.validate().is_err());
    input = snapshot("org-a");
    input.unit_multiplier = Decimal::from(1000);
    assert!(input.validate().is_err());
}
#[test]
fn restricted_xbrl_and_external_entities() {
    let xml = br#"<xbrl xmlns:us="http://fasb.org/us-gaap/2025"><us:Revenues contextRef="c1" unitRef="USD">1000</us:Revenues></xbrl>"#;
    match extract_xbrl_facts(xml) {
        Ok(facts) => {
            assert_eq!(facts.len(), 1);
            assert_eq!(facts[0].value, Decimal::from(1000));
        }
        Err(_) => panic!("standard filing fixture"),
    }
    assert!(
        extract_xbrl_facts(br#"<!DOCTYPE x [<!ENTITY e SYSTEM "file:///private">]><x/>"#).is_err()
    );
    assert!(extract_xbrl_facts(br#"<x xmlns:z="https://unsupported.example"><z:Value contextRef="c" unitRef="USD">1</z:Value></x>"#).is_err());
    assert!(
        extract_xbrl_facts(format!("{}{}", "<x>".repeat(65), "</x>".repeat(65)).as_bytes())
            .is_err()
    );
}
#[test]
fn circuit_opens_after_five_failed_operations() {
    let breaker = CircuitBreaker::default();
    for _ in 0..5 {
        assert!(breaker.acquire().is_ok());
        breaker.outcome(false);
    }
    assert!(matches!(breaker.acquire(), Err(PipelineError::CircuitOpen)));
    breaker.outcome(true);
    assert!(breaker.acquire().is_ok());
}

#[test]
fn fmp_normalization_preserves_dates_missing_and_currency() -> Result<(), Box<dyn std::error::Error>>
{
    let input = serde_json::json!([
        {"symbol":"0157.KL","reportedCurrency":"MYR","date":"2024-12-31","period":"FY","revenue":1000,"netIncome":-200},
        {"symbol":"0157.KL","reportedCurrency":"MYR","date":"2024-12-31","period":"FY","totalCurrentAssets":200}
    ]);
    let snapshot = normalize_fmp_statements("org-a", "0157.KL", &input)?;
    if let Dataset::Statements { periods } = snapshot.dataset {
        assert_eq!(periods.len(), 1);
        assert_eq!(periods[0].accounts["net_income"], Decimal::from(-200));
        assert!(!periods[0].accounts.contains_key("ebitda"));
    } else {
        panic!("statement expected");
    }
    let mut mixed = input.clone();
    mixed[1]["reportedCurrency"] = serde_json::json!("USD");
    assert!(normalize_fmp_statements("org-a", "0157.KL", &mixed).is_err());
    Ok(())
}

#[test]
fn artifacts_are_opaque_hash_verified_and_scoped() -> Result<(), Box<dyn std::error::Error>> {
    let root = tempfile::tempdir()?;
    let store = SnapshotStore::new(root.path(), "org-a")?;
    let other = SnapshotStore::new(root.path(), "org-b")?;
    let value = serde_json::json!({"summary":{"mean":100}});
    let id = store.commit_artifact(&value)?;
    assert!(valid_id(&id));
    assert_eq!(store.read_artifact(&id)?, value);
    assert!(other.read_artifact(&id).is_err());
    assert!(store.read_artifact("../../secret").is_err());
    std::fs::write(
        root.path()
            .join(digest(b"org-a"))
            .join(format!("{id}.artifact.json")),
        b"corrupt",
    )?;
    assert!(store.read_artifact(&id).is_err());
    Ok(())
}
