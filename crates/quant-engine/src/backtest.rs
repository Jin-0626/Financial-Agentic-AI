//! Offline prior-close strategies with checked decimal accounting.
use crate::{finite, QuantError};
use chrono::{Datelike, NaiveDate};
use rust_decimal::{prelude::ToPrimitive, Decimal, RoundingStrategy};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use std::str::FromStr;
use tokio_util::sync::CancellationToken;

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum BacktestStrategy {
    BuyAndHold,
    MovingAverageCrossover,
    Rsi,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct BacktestInput {
    pub prices: Vec<f64>,
    #[schemars(with = "Vec<String>")]
    pub dates: Vec<NaiveDate>,
    pub strategy: BacktestStrategy,
    #[schemars(with = "String")]
    pub initial_cash: Decimal,
    pub cost_bps: f64,
    pub currency: Option<String>,
    pub fast_window: usize,
    pub slow_window: usize,
    pub rsi_window: usize,
    pub lower: f64,
    pub upper: f64,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct BacktestEquity {
    #[schemars(with = "String")]
    pub date: NaiveDate,
    pub equity: String,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct BacktestTrade {
    #[schemars(with = "String")]
    pub date: NaiveDate,
    pub side: String,
    pub quantity: String,
    pub price: String,
    pub cost: String,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct BacktestMetrics {
    pub maximum_drawdown: f64,
    pub trade_count: usize,
    pub total_costs: String,
    pub open_position: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct BacktestResult {
    pub strategy: BacktestStrategy,
    pub initial_cash: String,
    pub final_equity: String,
    pub total_return: f64,
    pub equity: Vec<BacktestEquity>,
    pub trades: Vec<BacktestTrade>,
    pub assumptions: Vec<String>,
    pub metrics: BacktestMetrics,
}
fn add(a: Decimal, b: Decimal) -> Result<Decimal, QuantError> {
    a.checked_add(b).ok_or(QuantError::Overflow)
}
fn mul(a: Decimal, b: Decimal) -> Result<Decimal, QuantError> {
    let value = a.checked_mul(b).ok_or(QuantError::Overflow)?;
    if value.is_zero() && !a.is_zero() && !b.is_zero() {
        return Err(QuantError::Overflow);
    }
    Ok(value)
}
fn div(a: Decimal, b: Decimal) -> Result<Decimal, QuantError> {
    let value = a.checked_div(b).ok_or(QuantError::Overflow)?;
    if value.is_zero() && !a.is_zero() {
        return Err(QuantError::Overflow);
    }
    Ok(value)
}
fn decimal(value: f64) -> Result<Decimal, QuantError> {
    let converted = Decimal::from_scientific(&value.to_string())
        .or_else(|_| Decimal::from_str(&value.to_string()))
        .map_err(|_| QuantError::Overflow)?;
    if value != 0.0 && converted.is_zero() {
        return Err(QuantError::Overflow);
    }
    Ok(converted)
}
fn money(value: Decimal) -> Result<String, QuantError> {
    if value.abs() >= Decimal::from(100_000_000_000_000_000_000u128) {
        return Err(QuantError::Overflow);
    }
    Ok(format!(
        "{:.8}",
        value.round_dp_with_strategy(8, RoundingStrategy::MidpointNearestEven)
    ))
}
fn float(value: Decimal) -> Result<f64, QuantError> {
    finite(value.to_f64().ok_or(QuantError::Overflow)?)
}
// Compensated initial sums, followed by O(n) rolling updates.
fn sum(values: impl Iterator<Item = f64>) -> f64 {
    let (mut total, mut correction) = (0.0f64, 0.0f64);
    for value in values {
        let next = total + value;
        correction += if total.abs() >= value.abs() {
            (total - next) + value
        } else {
            (value - next) + total
        };
        total = next;
    }
    total + correction
}
fn average(prices: &[f64], window: usize) -> Result<Vec<Option<f64>>, QuantError> {
    let mut output = vec![None; prices.len()];
    let mut total = finite(sum(prices[..window].iter().copied()))?;
    output[window - 1] = Some(finite(total / window as f64)?);
    for i in window..prices.len() {
        total = finite(total + (prices[i] - prices[i - window]))?;
        output[i] = Some(finite(total / window as f64)?);
    }
    Ok(output)
}
fn strength(prices: &[f64], window: usize) -> Result<Vec<Option<f64>>, QuantError> {
    let changes: Vec<_> = prices.windows(2).map(|p| p[1] - p[0]).collect();
    let mut gain = finite(sum(changes[..window].iter().map(|x| x.max(0.0))) / window as f64)?;
    let mut loss = finite(sum(changes[..window].iter().map(|x| (-x).max(0.0))) / window as f64)?;
    let value = |gain: f64, loss: f64| {
        if gain == 0.0 && loss == 0.0 {
            50.0
        } else if loss == 0.0 {
            100.0
        } else {
            100.0 - 100.0 / (1.0 + gain / loss)
        }
    };
    let mut output = vec![None; prices.len()];
    output[window] = Some(finite(value(gain, loss))?);
    for (i, change) in changes.iter().enumerate().skip(window) {
        gain = finite(((window - 1) as f64 * gain + change.max(0.0)) / window as f64)?;
        loss = finite(((window - 1) as f64 * loss + (-change).max(0.0)) / window as f64)?;
        output[i + 1] = Some(finite(value(gain, loss))?);
    }
    Ok(output)
}
impl BacktestInput {
    pub fn validate(&self) -> Result<(), QuantError> {
        if !(3..=10000).contains(&self.prices.len())
            || self.prices.len() != self.dates.len()
            || self.prices.iter().any(|p| !p.is_finite() || *p <= 0.0)
            || self.dates.windows(2).any(|d| d[0] >= d[1])
            || self.dates.iter().any(|d| !(1..=9999).contains(&d.year()))
            || self.initial_cash <= Decimal::ZERO
            || !self.cost_bps.is_finite()
            || !(0.0..=1000.0).contains(&self.cost_bps)
            || !(2..=2520).contains(&self.fast_window)
            || !(3..=2520).contains(&self.slow_window)
            || self.fast_window >= self.slow_window
            || !(2..=2520).contains(&self.rsi_window)
            || !self.lower.is_finite()
            || !self.upper.is_finite()
            || !(0.0..=100.0).contains(&self.lower)
            || !(0.0..=100.0).contains(&self.upper)
            || self.lower >= self.upper
            || self
                .currency
                .as_ref()
                .is_some_and(|c| c.len() != 3 || !c.bytes().all(|b| b.is_ascii_uppercase()))
            || matches!(self.strategy, BacktestStrategy::MovingAverageCrossover)
                && self.prices.len() <= self.slow_window
            || matches!(self.strategy, BacktestStrategy::Rsi)
                && self.prices.len() <= self.rsi_window
        {
            return Err(QuantError::InvalidInput);
        }
        money(self.initial_cash)?;
        Ok(())
    }
}
pub fn offline_backtest(
    a: &BacktestInput,
    cancel: &CancellationToken,
) -> Result<BacktestResult, QuantError> {
    a.validate()?;
    if cancel.is_cancelled() {
        return Err(QuantError::Cancelled);
    }
    let averages = if matches!(a.strategy, BacktestStrategy::MovingAverageCrossover) {
        Some((
            average(&a.prices, a.fast_window)?,
            average(&a.prices, a.slow_window)?,
        ))
    } else {
        None
    };
    let rsi = if matches!(a.strategy, BacktestStrategy::Rsi) {
        Some(strength(&a.prices, a.rsi_window)?)
    } else {
        None
    };
    let fee = div(decimal(a.cost_bps)?, Decimal::from(10000))?;
    let (mut cash, mut quantity) = (a.initial_cash, Decimal::ZERO);
    let (mut target, mut peak, mut drawdown) = (
        false,
        Decimal::from_str(&money(a.initial_cash)?).map_err(|_| QuantError::Overflow)?,
        Decimal::ZERO,
    );
    let mut equity = Vec::with_capacity(a.prices.len());
    let mut trades = Vec::new();
    let mut costs = Decimal::ZERO;
    let mut value = cash;
    for (i, &raw_price) in a.prices.iter().enumerate() {
        if cancel.is_cancelled() {
            return Err(QuantError::Cancelled);
        }
        let price = decimal(raw_price)?;
        // Use the target set on the PREVIOUS bar; update signals after execution.
        if target && quantity.is_zero() {
            quantity = div(cash, mul(price, add(Decimal::ONE, fee)?)?)?;
            let cost = mul(mul(quantity, price)?, fee)?;
            cash = Decimal::ZERO;
            trades.push(BacktestTrade {
                date: a.dates[i],
                side: "BUY".into(),
                quantity: money(quantity)?,
                price: money(price)?,
                cost: money(cost)?,
            });
        } else if !target && !quantity.is_zero() {
            let proceeds = mul(quantity, price)?;
            let cost = mul(proceeds, fee)?;
            cash = add(
                cash,
                proceeds.checked_sub(cost).ok_or(QuantError::Overflow)?,
            )?;
            trades.push(BacktestTrade {
                date: a.dates[i],
                side: "SELL".into(),
                quantity: money(quantity)?,
                price: money(price)?,
                cost: money(cost)?,
            });
            quantity = Decimal::ZERO;
        }
        if let Some(trade) = trades.last().filter(|t| t.date == a.dates[i]) {
            costs = add(
                costs,
                Decimal::from_str(&trade.cost).map_err(|_| QuantError::Overflow)?,
            )?;
        }
        value = add(cash, mul(quantity, price)?)?;
        let rounded = money(value)?;
        let observed = Decimal::from_str(&rounded).map_err(|_| QuantError::Overflow)?;
        peak = peak.max(observed);
        if !peak.is_zero() {
            drawdown = drawdown.min(
                div(observed, peak)?
                    .checked_sub(Decimal::ONE)
                    .ok_or(QuantError::Overflow)?,
            );
        }
        equity.push(BacktestEquity {
            date: a.dates[i],
            equity: rounded,
        });
        match a.strategy {
            BacktestStrategy::BuyAndHold => target = true,
            BacktestStrategy::MovingAverageCrossover => {
                if let Some((fast, slow)) = &averages {
                    if let (Some(f), Some(s)) = (fast[i], slow[i]) {
                        target = f > s;
                    }
                }
            }
            BacktestStrategy::Rsi => {
                if let Some(Some(r)) = rsi.as_ref().map(|series| series[i]) {
                    if r < a.lower {
                        target = true;
                    } else if r > a.upper {
                        target = false;
                    }
                }
            }
        }
    }
    Ok(BacktestResult {
        strategy: a.strategy.clone(),
        initial_cash: money(a.initial_cash)?,
        final_equity: money(value)?,
        total_return: float(
            div(value, a.initial_cash)?
                .checked_sub(Decimal::ONE)
                .ok_or(QuantError::Overflow)?,
        )?,
        equity,
        metrics: BacktestMetrics {
            maximum_drawdown: float(drawdown)?,
            trade_count: trades.len(),
            total_costs: format!("{}", costs),
            open_position: !quantity.is_zero(),
        },
        trades,
        assumptions: vec![
            "Signals from prior close execute at next close".into(),
            "Fractional shares, long-only, no leverage".into(),
            "Cost charged on each trade".into(),
            "Final holdings marked to market; no forced liquidation".into(),
        ],
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn input(
        prices: Vec<f64>,
        strategy: BacktestStrategy,
    ) -> Result<BacktestInput, Box<dyn std::error::Error>> {
        let start = NaiveDate::from_ymd_opt(2025, 1, 1).ok_or("invalid fixture date")?;
        let dates = (0..prices.len())
            .map(|i| start + chrono::Duration::days(i as i64))
            .collect();
        Ok(BacktestInput {
            prices,
            dates,
            strategy,
            initial_cash: Decimal::from(1000),
            cost_bps: 0.0,
            currency: Some("USD".into()),
            fast_window: 2,
            slow_window: 3,
            rsi_window: 2,
            lower: 30.0,
            upper: 70.0,
        })
    }
    #[test]
    fn prior_close_costs_and_mark_to_market() -> Result<(), Box<dyn std::error::Error>> {
        let mut a = input(vec![10.0, 20.0, 30.0, 40.0], BacktestStrategy::BuyAndHold)?;
        a.cost_bps = 100.0;
        let r = offline_backtest(&a, &CancellationToken::new())?;
        assert_eq!(r.trades.len(), 1);
        assert_eq!(r.trades[0].date, a.dates[1]);
        assert_eq!(r.trades[0].price, "20.00000000");
        assert_eq!(r.trades[0].cost, "9.90099010");
        assert_eq!(r.final_equity, "1980.19801980");
        assert!(r.metrics.open_position);
        a.prices[3] = 400.0;
        let changed = offline_backtest(&a, &CancellationToken::new())?;
        assert_eq!(
            serde_json::to_value(&r.equity[..3])?,
            serde_json::to_value(&changed.equity[..3])?
        );
        Ok(())
    }
    #[test]
    fn rsi_thresholds_and_flat_series() -> Result<(), Box<dyn std::error::Error>> {
        let a = input(
            vec![100.0, 90.0, 80.0, 90.0, 110.0, 120.0],
            BacktestStrategy::Rsi,
        )?;
        let r = offline_backtest(&a, &CancellationToken::new())?;
        assert_eq!(r.trades[0].date, a.dates[3]);
        assert_eq!(r.trades[0].side, "BUY");
        assert_eq!(r.trades[1].date, a.dates[5]);
        assert_eq!(r.trades[1].side, "SELL");
        let flat = input(vec![100.0; 10], BacktestStrategy::Rsi)?;
        assert!(offline_backtest(&flat, &CancellationToken::new())?
            .trades
            .is_empty());
        Ok(())
    }
    #[test]
    fn crossover_warmup_uses_next_bar() -> Result<(), Box<dyn std::error::Error>> {
        let a = input(
            vec![100.0, 101.0, 102.0, 103.0, 90.0, 80.0, 70.0],
            BacktestStrategy::MovingAverageCrossover,
        )?;
        let r = offline_backtest(&a, &CancellationToken::new())?;
        assert_eq!(r.trades[0].date, a.dates[3]);
        assert_eq!(r.trades[1].date, a.dates[5]);
        assert!(!r.metrics.open_position);
        Ok(())
    }
    #[test]
    fn decimal_underflow_is_an_error_instead_of_a_zero_trade() {
        assert!(decimal(1e-40).is_err());
        assert!(mul(Decimal::new(1, 28), Decimal::new(1, 28)).is_err());
        assert!(div(Decimal::new(1, 28), Decimal::from(10)).is_err());
        assert_eq!(mul(Decimal::ZERO, Decimal::ONE), Ok(Decimal::ZERO));
    }
    #[test]
    fn rejects_invalid_inputs_and_honors_cancellation() -> Result<(), Box<dyn std::error::Error>> {
        let mut a = input(vec![100.0; 10], BacktestStrategy::BuyAndHold)?;
        let cancel = CancellationToken::new();
        cancel.cancel();
        assert!(matches!(
            offline_backtest(&a, &cancel),
            Err(QuantError::Cancelled)
        ));
        a.dates[1] = a.dates[0];
        assert!(matches!(
            offline_backtest(&a, &CancellationToken::new()),
            Err(QuantError::InvalidInput)
        ));
        a = input(vec![100.0; 10], BacktestStrategy::BuyAndHold)?;
        a.prices[2] = f64::NAN;
        assert!(a.validate().is_err());
        a.prices[2] = 0.0;
        assert!(a.validate().is_err());
        a.prices[2] = 100.0;
        a.initial_cash = Decimal::MAX;
        assert!(matches!(a.validate(), Err(QuantError::Overflow)));
        Ok(())
    }
}
