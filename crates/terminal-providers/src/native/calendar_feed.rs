//! Current-week schedule with reference-period and release-vintage matched actuals.
use super::*;
use chrono::Months;

#[derive(Clone, Copy)]
enum Method {
    Inflation,
    Change,
    Daily,
}
struct Series {
    id: &'static str,
    method: Method,
    publisher: &'static str,
}
fn series(row: &Value) -> Option<Series> {
    let title = row["title"].as_str()?.to_ascii_lowercase();
    let country = row["country"].as_str()?;
    let (id, method, publisher) = if country == "USD" && title.contains("cpi") {
        (
            if title.contains("core") {
                "CPILFESL"
            } else {
                "CPIAUCSL"
            },
            Method::Inflation,
            "US Bureau of Labor Statistics",
        )
    } else if country == "USD" && (title.contains("non-farm") || title.contains("nonfarm")) {
        ("PAYEMS", Method::Change, "US Bureau of Labor Statistics")
    } else if country == "USD" && title.contains("federal funds") {
        (
            "DFEDTARU",
            Method::Daily,
            "Board of Governors of the Federal Reserve System",
        )
    } else if country == "EUR" && title.contains("refinancing") {
        ("ECBMRRFR", Method::Daily, "European Central Bank")
    } else if country == "EUR" && title.contains("deposit facility") {
        ("ECBDFR", Method::Daily, "European Central Bank")
    } else {
        return None;
    };
    Some(Series {
        id,
        method,
        publisher,
    })
}
fn amount(value: &Value) -> Option<Decimal> {
    decimal(value)?.parse().ok()
}
fn actual(row: &Value, observations: &Value) -> Option<Value> {
    let spec = series(row)?;
    let period = row["reference_period"].as_str()?;
    let daily = matches!(spec.method, Method::Daily);
    let date = NaiveDate::parse_from_str(
        &if daily {
            period.to_owned()
        } else {
            format!("{period}-01")
        },
        "%Y-%m-%d",
    )
    .ok()?;
    if (daily && date.format("%Y-%m-%d").to_string() != period)
        || (!daily && date.format("%Y-%m").to_string() != period)
    {
        return None;
    }
    let observations = observations.as_array()?;
    let find = |prefix: &str, exact: bool| {
        observations.iter().find(|obs| {
            obs["value"] != "."
                && obs["date"].as_str().is_some_and(|date| {
                    if exact {
                        date == prefix
                    } else {
                        date.starts_with(&format!("{prefix}-"))
                    }
                })
        })
    };
    let target = amount(&find(period, daily)?["value"])?;
    let (value, units) = match spec.method {
        Method::Daily => (target, "percent"),
        Method::Inflation | Method::Change => {
            let title = row["title"].as_str()?.to_ascii_lowercase();
            let months = if matches!(spec.method, Method::Inflation) && title.contains("y/y") {
                12
            } else if matches!(spec.method, Method::Change) || title.contains("m/m") {
                1
            } else {
                return None;
            };
            let previous = date
                .checked_sub_months(Months::new(months))?
                .format("%Y-%m")
                .to_string();
            let base = amount(&find(&previous, false)?["value"])?;
            if matches!(spec.method, Method::Change) {
                (target.checked_sub(base)?, "thousand persons")
            } else {
                (
                    target
                        .checked_div(base)?
                        .checked_sub(Decimal::ONE)?
                        .checked_mul(Decimal::from(100))?,
                    "percent",
                )
            }
        }
    };
    let rounded =
        value.round_dp_with_strategy(1, rust_decimal::RoundingStrategy::MidpointNearestEven);
    let display = format!(
        "{rounded:.1}{}",
        if units == "percent" {
            "%"
        } else {
            " thousand persons"
        }
    );
    Some(
        json!({"value":display,"units":units,"reference_period":period,"source":format!("FRED / {}",spec.publisher),"series_id":spec.id,"url":format!("https://fred.stlouisfed.org/series/{}",spec.id)}),
    )
}
fn nonempty(value: &&Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_some_and(|v| v != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}
fn event(row: &Value, outcome: Option<Value>) -> Value {
    json!({"title":row.get("title"),"country":row.get("country"),"date":row.get("date"),"impact":row.get("impact"),"forecast":row.get("forecast").filter(nonempty),"previous":row.get("previous").filter(nonempty),"actual":outcome.as_ref().map(|v|&v["value"]),"actual_status":if outcome.is_some() {"Reference-period-matched official observation"} else {"No event-matched release observation verified"},"actual_evidence":outcome})
}
impl NativeMarketProvider {
    pub async fn calendar_value(&self) -> Result<Value, ProviderError> {
        let raw = self
            .get(
                "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
                &[],
            )
            .await?;
        let rows = raw
            .as_array()
            .ok_or_else(|| payload("Invalid economic calendar response"))?;
        let mut events = Vec::new();
        let mut observations = BTreeMap::new();
        for row in rows.iter().take(500) {
            if !row.is_object() {
                return Err(payload("Invalid economic calendar event"));
            }
            let mut outcome = None;
            if self.fred_key.is_some() && row["reference_period"].is_string() {
                if let (Some(spec), Some(when)) = (
                    series(row),
                    row["date"]
                        .as_str()
                        .and_then(|v| DateTime::parse_from_rfc3339(v).ok()),
                ) {
                    if when <= Utc::now() {
                        let vintage = row["date"]
                            .as_str()
                            .and_then(|v| v.get(..10))
                            .ok_or_else(|| payload("Invalid release date"))?;
                        let key = (spec.id.to_owned(), vintage.to_owned());
                        if !observations.contains_key(&key) {
                            let values = self
                                .fred_observations(spec.id, Some(vintage))
                                .await
                                .unwrap_or(Value::Null);
                            observations.insert(key.clone(), values);
                        }
                        outcome = observations
                            .get(&key)
                            .and_then(|values| actual(row, values));
                        if let Some(value) = &mut outcome {
                            value["vintage_date"] = json!(vintage);
                        }
                    }
                }
            }
            events.push(event(row, outcome));
        }
        let mut output = envelope(
            json!(events),
            "Forex Factory public calendar / FRED where matched",
            "Current-week public schedule; actual outcomes require event-matched official evidence",
        );
        output["limitations"]=json!(["Malaysian release coverage may be absent; many events lack the reference period needed for safe actual matching"]);
        Ok(output)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn actuals_require_reference_period_and_never_use_feed_actual(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let fixture: Value =
            serde_json::from_str(include_str!("../../tests/fixtures/calendar-actuals.json"))?;
        for case in fixture.as_array().ok_or("fixture")? {
            assert_eq!(
                actual(&case["event"], &case["observations"]),
                case["expected"]
                    .as_object()
                    .map(|_| case["expected"].clone())
            );
        }
        let missing = event(
            &json!({"title":"CPI y/y","country":"USD","actual":"99%"}),
            None,
        );
        assert!(missing["actual"].is_null());
        assert!(missing["actual_evidence"].is_null());
        Ok(())
    }
}
