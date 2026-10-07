use chrono::NaiveDate;
use quant_engine::*;
use rust_decimal::Decimal;
use std::{collections::BTreeMap, str::FromStr};
use tokio_util::sync::CancellationToken;
fn decimal(value: &str) -> Decimal {
    match Decimal::from_str(value) {
        Ok(value) => value,
        Err(_) => panic!("test decimal"),
    }
}
fn date(value: &str) -> NaiveDate {
    match NaiveDate::parse_from_str(value, "%Y-%m-%d") {
        Ok(value) => value,
        Err(_) => panic!("test date"),
    }
}
fn input(flow: &str) -> DcfInput {
    DcfInput {
        forecast: Forecast {
            valuation_date: date("2025-01-01"),
            cash_flows: vec![DatedCashFlow {
                date: date("2026-01-01"),
                amount: decimal(flow),
            }],
            net_debt: decimal("50"),
            diluted_shares: decimal("10"),
        },
        wacc: decimal("0.1"),
        terminal_growth_rate: decimal("0"),
    }
}
#[test]
fn dcf_independent_one_year_fixture() {
    let result = discounted_cash_flow(&input("100"));
    assert!(result.is_ok());
    if let Ok(result) = result {
        assert!((result.enterprise_value - decimal("1000")).abs() < decimal("0.00001"));
        assert!((result.value_per_share - decimal("95")).abs() < decimal("0.00001"));
    }
}
#[test]
fn dcf_preserves_negative_cash_flows_and_values() {
    match discounted_cash_flow(&input("-100")) {
        Ok(value) => assert!(value.value_per_share < Decimal::ZERO),
        Err(_) => panic!("negative cash flow must remain valid"),
    }
}
#[test]
fn dcf_rejects_invalid_assumptions_and_overflow() {
    let mut model = input("100");
    model.terminal_growth_rate = model.wacc;
    assert_eq!(
        discounted_cash_flow(&model).err(),
        Some(QuantError::InvalidInput)
    );
    model = input("100");
    model.forecast.diluted_shares = Decimal::ZERO;
    assert_eq!(
        discounted_cash_flow(&model).err(),
        Some(QuantError::InvalidInput)
    );
    model = input("100");
    model
        .forecast
        .cash_flows
        .push(model.forecast.cash_flows[0].clone());
    assert_eq!(
        discounted_cash_flow(&model).err(),
        Some(QuantError::InvalidInput)
    );
    model = input("79228162514264337593543950335");
    assert_eq!(
        discounted_cash_flow(&model).err(),
        Some(QuantError::Overflow)
    );
}
#[test]
fn risk_nearest_rank_and_inclusive_tail_match_hand_calculation() {
    match historical_var(&[0.01, -0.02, 0.03, -0.04], 0.75) {
        Ok(result) => {
            assert!((result.var - 0.02).abs() < 1e-12);
            assert!((result.expected_shortfall - 0.03).abs() < 1e-12);
        }
        Err(_) => panic!("valid risk fixture"),
    }
    match historical_var(&[0.01, 0.02], 0.95) {
        Ok(result) => assert!(result.var < 0.0),
        Err(_) => panic!("signed losses valid"),
    }
}
#[test]
fn normal_risk_and_bad_numeric_edges() {
    match parametric_var(&[-0.02, 0.02], 0.5) {
        Ok(result) => {
            assert!(result.var.abs() < 1e-10);
            assert!(result.expected_shortfall > result.var);
        }
        Err(_) => panic!("normal fixture"),
    }
    for confidence in [0.0, 1.0, f64::NAN, f64::INFINITY] {
        assert!(historical_var(&[0.0, 0.01], confidence).is_err());
    }
    assert!(historical_var(&[0.0, f64::NAN], 0.95).is_err());
    assert!(historical_var(&[0.0], 0.95).is_err());
}
#[test]
fn ratios_match_python_contract_and_preserve_missing_values() {
    let accounts = BTreeMap::from([
        ("revenue".into(), decimal("1000")),
        ("gross_profit".into(), decimal("400")),
        ("operating_income".into(), decimal("100")),
        ("net_income".into(), decimal("-20")),
        ("current_assets".into(), decimal("200")),
        ("current_liabilities".into(), decimal("100")),
    ]);
    let statements = FinancialStatements {
        period: date("2025-12-31"),
        annual: true,
        accounts,
    };
    match financial_ratios(&statements) {
        Ok(result) => {
            assert_eq!(result.ratios["gross_margin_pct"].value, Some(decimal("40")));
            assert_eq!(result.ratios["net_margin_pct"].value, Some(decimal("-2")));
            assert_eq!(result.ratios["current_ratio"].value, Some(decimal("2")));
            assert_eq!(result.ratios["ebitda_margin_pct"].value, None);
        }
        Err(_) => panic!("ratio fixture"),
    }
    let mut zero = statements;
    zero.accounts
        .insert("current_liabilities".into(), Decimal::ZERO);
    match financial_ratios(&zero) {
        Ok(result) => assert!(result.ratios["current_ratio"].unavailable_reason.is_some()),
        Err(_) => panic!("missing denominator valid"),
    }
}
#[test]
fn monte_carlo_seed_cancel_and_degenerate_volatility() {
    let input = MonteCarloInput {
        spot: 100.0,
        log_returns: vec![0.0, 0.0, 0.0],
        simulations: 1000,
        horizon_days: 20,
        seed: 42,
    };
    let first = monte_carlo(&input, &CancellationToken::new());
    let second = monte_carlo(&input, &CancellationToken::new());
    assert_eq!(
        serde_json::to_value(first.ok()).ok(),
        serde_json::to_value(second.ok()).ok()
    );
    match monte_carlo(&input, &CancellationToken::new()) {
        Ok(value) => assert_eq!(value.terminal_p50, 100.0),
        Err(_) => panic!("constant scenario"),
    }
    let token = CancellationToken::new();
    token.cancel();
    assert_eq!(
        monte_carlo(&input, &token).err(),
        Some(QuantError::Cancelled)
    );
    let mut stochastic = input;
    stochastic.log_returns = vec![-0.02, 0.01, 0.03];
    match (
        monte_carlo(&stochastic, &CancellationToken::new()),
        monte_carlo(&stochastic, &CancellationToken::new()),
    ) {
        (Ok(a), Ok(b)) => {
            assert_eq!(a.terminal_p95, b.terminal_p95);
            assert!(a.terminal_p05 < a.terminal_p95);
        }
        _ => panic!("stochastic fixture"),
    }
}
#[test]
fn indicators_and_portfolio_alignment() {
    assert_eq!(
        simple_moving_average(&[1.0, 2.0, 3.0, 4.0], 2).ok(),
        Some(vec![1.5, 2.5, 3.5])
    );
    assert_eq!(
        relative_strength_index(&[1.0, 1.0, 1.0, 1.0], 2).ok(),
        Some(vec![50.0, 50.0])
    );
    assert_eq!(
        relative_strength_index(&[1.0, 2.0, 3.0, 4.0], 2).ok(),
        Some(vec![100.0, 100.0])
    );
    assert_eq!(
        portfolio_returns(&[vec![0.1, -0.1], vec![0.0, 0.2]], &[0.5, 0.5]).ok(),
        Some(vec![0.05, 0.05])
    );
    assert!(portfolio_returns(&[vec![0.1], vec![0.1, 0.2]], &[0.5, 0.5]).is_err());
    assert!(portfolio_returns(&[vec![0.1, 0.2]], &[0.5]).is_err());
}
