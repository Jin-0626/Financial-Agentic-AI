use crate::QuantError;
use chrono::NaiveDate;
use rust_decimal::{Decimal, MathematicalOps};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DatedCashFlow {
    #[schemars(with = "String")]
    pub date: NaiveDate,
    #[schemars(with = "String")]
    pub amount: Decimal,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Forecast {
    #[schemars(with = "String")]
    pub valuation_date: NaiveDate,
    pub cash_flows: Vec<DatedCashFlow>,
    #[schemars(with = "String")]
    pub net_debt: Decimal,
    #[schemars(with = "String")]
    pub diluted_shares: Decimal,
}
impl Forecast {
    pub fn validate(&self) -> Result<(), QuantError> {
        if self.cash_flows.is_empty()
            || self.cash_flows.len() > 50
            || self.diluted_shares <= Decimal::ZERO
        {
            return Err(QuantError::InvalidInput);
        }
        let mut previous = self.valuation_date;
        for flow in &self.cash_flows {
            if flow.date <= previous || (flow.date - self.valuation_date).num_days() > 18_263 {
                return Err(QuantError::InvalidInput);
            }
            previous = flow.date;
        }
        Ok(())
    }
}
#[derive(Debug, Clone)]
pub struct DcfInput {
    pub forecast: Forecast,
    pub wacc: Decimal,
    pub terminal_growth_rate: Decimal,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DcfResult {
    #[schemars(with = "String")]
    pub present_value_cash_flows: Decimal,
    #[schemars(with = "String")]
    pub present_value_terminal: Decimal,
    #[schemars(with = "String")]
    pub enterprise_value: Decimal,
    #[schemars(with = "String")]
    pub equity_value: Decimal,
    #[schemars(with = "String")]
    pub value_per_share: Decimal,
    pub convention: String,
}
pub fn discounted_cash_flow(input: &DcfInput) -> Result<DcfResult, QuantError> {
    input.forecast.validate()?;
    if input.wacc <= input.terminal_growth_rate
        || input.wacc <= Decimal::ZERO
        || input.wacc > Decimal::ONE
        || input.terminal_growth_rate <= -Decimal::ONE
        || input.terminal_growth_rate >= Decimal::ONE
    {
        return Err(QuantError::InvalidInput);
    }
    let base = Decimal::ONE
        .checked_add(input.wacc)
        .ok_or(QuantError::Overflow)?;
    let mut present = Decimal::ZERO;
    let mut final_discount = Decimal::ONE;
    for flow in &input.forecast.cash_flows {
        let time = Decimal::from((flow.date - input.forecast.valuation_date).num_days())
            .checked_div(Decimal::from(365))
            .ok_or(QuantError::Overflow)?;
        final_discount = base.checked_powd(time).ok_or(QuantError::Overflow)?;
        let value = flow
            .amount
            .checked_div(final_discount)
            .ok_or(QuantError::Overflow)?;
        present = present.checked_add(value).ok_or(QuantError::Overflow)?;
    }
    let last = input
        .forecast
        .cash_flows
        .last()
        .ok_or(QuantError::InvalidInput)?;
    let growth = Decimal::ONE
        .checked_add(input.terminal_growth_rate)
        .ok_or(QuantError::Overflow)?;
    let spread = input
        .wacc
        .checked_sub(input.terminal_growth_rate)
        .ok_or(QuantError::Overflow)?;
    let terminal = last
        .amount
        .checked_mul(growth)
        .and_then(|x| x.checked_div(spread))
        .and_then(|x| x.checked_div(final_discount))
        .ok_or(QuantError::Overflow)?;
    let enterprise = present.checked_add(terminal).ok_or(QuantError::Overflow)?;
    let equity = enterprise
        .checked_sub(input.forecast.net_debt)
        .ok_or(QuantError::Overflow)?;
    let per_share = equity
        .checked_div(input.forecast.diluted_shares)
        .ok_or(QuantError::Overflow)?;
    Ok(DcfResult {
        present_value_cash_flows: present,
        present_value_terminal: terminal,
        enterprise_value: enterprise,
        equity_value: equity,
        value_per_share: per_share,
        convention: "ACT/365; annual unlevered cash flows; terminal value at final forecast date"
            .into(),
    })
}
