use crate::QuantError;
use chrono::NaiveDate;
use rust_decimal::Decimal;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct FinancialStatements {
    #[schemars(with = "String")]
    pub period: NaiveDate,
    pub annual: bool,
    #[schemars(with = "BTreeMap<String, String>")]
    pub accounts: BTreeMap<String, Decimal>,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RatioValue {
    #[schemars(with = "Option<String>")]
    pub value: Option<Decimal>,
    pub unit: String,
    pub unavailable_reason: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RatioResult {
    pub ratios: BTreeMap<String, RatioValue>,
}
pub fn financial_ratios(statements: &FinancialStatements) -> Result<RatioResult, QuantError> {
    let definitions = [
        ("gross_margin_pct", "gross_profit", "revenue", true),
        ("operating_margin_pct", "operating_income", "revenue", true),
        ("net_margin_pct", "net_income", "revenue", true),
        ("ebitda_margin_pct", "ebitda", "revenue", true),
        (
            "current_ratio",
            "current_assets",
            "current_liabilities",
            false,
        ),
    ];
    let mut ratios = BTreeMap::new();
    for (name, numerator, denominator, percent) in definitions {
        let value = match (
            statements.accounts.get(numerator),
            statements.accounts.get(denominator),
        ) {
            (Some(a), Some(b)) if *b > Decimal::ZERO => Some(
                a.checked_div(*b)
                    .and_then(|x| x.checked_mul(Decimal::from(if percent { 100 } else { 1 })))
                    .ok_or(QuantError::Overflow)?,
            ),
            _ => None,
        };
        ratios.insert(
            name.into(),
            RatioValue {
                value,
                unit: if percent { "percent" } else { "ratio" }.into(),
                unavailable_reason: if value.is_none() {
                    Some("missing numerator or nonpositive/missing denominator".into())
                } else {
                    None
                },
            },
        );
    }
    Ok(RatioResult { ratios })
}
