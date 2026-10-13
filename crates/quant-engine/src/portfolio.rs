//! Transaction-based accounting and common-date performance. No provider or database I/O.
use crate::QuantError;
use chrono::{NaiveDate, Utc};
use rust_decimal::Decimal;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

type Result<T> = std::result::Result<T, QuantError>;
fn add(a: Decimal, b: Decimal) -> Result<Decimal> {
    a.checked_add(b).ok_or(QuantError::Overflow)
}
fn mul(a: Decimal, b: Decimal) -> Result<Decimal> {
    a.checked_mul(b).ok_or(QuantError::Overflow)
}
fn div(a: Decimal, b: Decimal) -> Result<Decimal> {
    a.checked_div(b).ok_or(QuantError::InvalidInput)
}
fn valid_symbol(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 32
        && s.bytes()
            .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b".^=-_".contains(&b))
}
fn valid_currency(s: &str) -> bool {
    s.len() == 3 && s.bytes().all(|b| b.is_ascii_uppercase())
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Holding {
    pub symbol: String,
    #[schemars(with = "String")]
    pub quantity: Decimal,
    #[schemars(with = "String")]
    pub average_cost: Decimal,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq)]
#[serde(rename_all = "UPPERCASE")]
pub enum Side {
    Buy,
    Sell,
    Dividend,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct PortfolioTrade {
    pub id: String,
    pub symbol: String,
    pub side: Side,
    #[schemars(with = "String")]
    pub quantity: Decimal,
    #[schemars(with = "String")]
    pub price: Decimal,
    #[schemars(with = "String")]
    pub date: NaiveDate,
    #[serde(default)]
    pub notes: String,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct LedgerInput {
    pub opening_positions: Vec<Holding>,
    pub transactions: Vec<PortfolioTrade>,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct PostedTrade {
    #[serde(flatten)]
    pub trade: PortfolioTrade,
    #[schemars(with = "String")]
    pub realized_gain: Decimal,
    #[schemars(with = "String")]
    pub total_value: Decimal,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct LedgerResult {
    pub positions: Vec<Holding>,
    pub transactions: Vec<PostedTrade>,
    #[schemars(with = "String")]
    pub realized_gain: Decimal,
    #[schemars(with = "String")]
    pub dividends: Decimal,
}
pub fn portfolio_ledger(input: &LedgerInput) -> Result<LedgerResult> {
    if input.opening_positions.len() > 50 || input.transactions.len() > 2000 {
        return Err(QuantError::InvalidInput);
    }
    let mut positions = BTreeMap::new();
    for p in &input.opening_positions {
        if !valid_symbol(&p.symbol)
            || p.quantity <= Decimal::ZERO
            || p.average_cost < Decimal::ZERO
            || positions.insert(p.symbol.clone(), p.clone()).is_some()
        {
            return Err(QuantError::InvalidInput);
        }
        mul(p.quantity, p.average_cost)?;
    }
    let mut ids = BTreeMap::<String, PortfolioTrade>::new();
    let mut transactions = Vec::new();
    let mut previous = None;
    let mut realized_gain = Decimal::ZERO;
    let mut dividends = Decimal::ZERO;
    for t in &input.transactions {
        if let Some(old) = ids.get(&t.id) {
            if old != t {
                return Err(QuantError::InvalidInput);
            }
            continue;
        }
        if t.id.is_empty()
            || t.id.len() > 64
            || !t
                .id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
            || !valid_symbol(&t.symbol)
            || t.quantity <= Decimal::ZERO
            || t.price < Decimal::ZERO
            || t.date > Utc::now().date_naive()
            || previous.is_some_and(|d| t.date < d)
            || t.notes.len() > 1000
        {
            return Err(QuantError::InvalidInput);
        }
        previous = Some(t.date);
        ids.insert(t.id.clone(), t.clone());
        let total = mul(t.quantity, t.price)?;
        let current = positions.get(&t.symbol).cloned();
        let mut realized = Decimal::ZERO;
        match t.side {
            Side::Buy => {
                let (qty, cost) = current.map_or((Decimal::ZERO, Decimal::ZERO), |p| {
                    (p.quantity, p.average_cost)
                });
                let quantity = add(qty, t.quantity)?;
                let average_cost = div(add(mul(qty, cost)?, total)?, quantity)?;
                positions.insert(
                    t.symbol.clone(),
                    Holding {
                        symbol: t.symbol.clone(),
                        quantity,
                        average_cost,
                    },
                );
            }
            Side::Sell => {
                let p = current.ok_or(QuantError::InvalidInput)?;
                if t.quantity > p.quantity {
                    return Err(QuantError::InvalidInput);
                }
                realized = mul(t.quantity, add(t.price, -p.average_cost)?)?;
                realized_gain = add(realized_gain, realized)?;
                let remaining = add(p.quantity, -t.quantity)?;
                if remaining == Decimal::ZERO {
                    positions.remove(&t.symbol);
                } else {
                    positions.insert(
                        t.symbol.clone(),
                        Holding {
                            quantity: remaining,
                            ..p
                        },
                    );
                }
            }
            Side::Dividend => {
                let p = current.ok_or(QuantError::InvalidInput)?;
                if t.quantity > p.quantity {
                    return Err(QuantError::InvalidInput);
                }
                dividends = add(dividends, total)?;
            }
        }
        if positions.len() > 50 {
            return Err(QuantError::InvalidInput);
        }
        transactions.push(PostedTrade {
            trade: t.clone(),
            realized_gain: realized,
            total_value: total,
        });
    }
    Ok(LedgerResult {
        positions: positions.into_values().collect(),
        transactions,
        realized_gain,
        dividends,
    })
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct PortfolioQuote {
    pub symbol: String,
    pub currency: String,
    #[schemars(with = "String")]
    pub price: Decimal,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct SummaryInput {
    #[serde(default)]
    pub sectors: BTreeMap<String, String>,
    pub currency: String,
    pub positions: Vec<Holding>,
    pub quotes: Vec<PortfolioQuote>,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct ValuedHolding {
    #[serde(flatten)]
    pub holding: Holding,
    #[schemars(with = "String")]
    pub market_value: Decimal,
    #[schemars(with = "String")]
    pub cost_basis: Decimal,
    #[schemars(with = "String")]
    pub unrealized_gain: Decimal,
    #[schemars(with = "String")]
    pub weight_percent: Decimal,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct SectorAllocation {
    pub name: String,
    #[schemars(with = "String")]
    pub market_value: Decimal,
    #[schemars(with = "String")]
    pub weight_percent: Decimal,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct PortfolioSummary {
    pub sectors: Vec<SectorAllocation>,
    pub positions: Vec<ValuedHolding>,
    #[schemars(with = "String")]
    pub market_value: Decimal,
    #[schemars(with = "String")]
    pub cost_basis: Decimal,
    #[schemars(with = "String")]
    pub unrealized_gain: Decimal,
}
pub fn portfolio_summary(input: &SummaryInput) -> Result<PortfolioSummary> {
    if !valid_currency(&input.currency)
        || input.quotes.len() > 50
        || input.sectors.len() > 50
        || input.sectors.values().any(|s| s.len() > 100)
    {
        return Err(QuantError::InvalidInput);
    }
    let ledger = portfolio_ledger(&LedgerInput {
        opening_positions: input.positions.clone(),
        transactions: vec![],
    })?;
    let mut quotes = BTreeMap::new();
    for q in &input.quotes {
        if !valid_symbol(&q.symbol)
            || q.currency != input.currency
            || q.price <= Decimal::ZERO
            || quotes.insert(&q.symbol, q.price).is_some()
        {
            return Err(QuantError::InvalidInput);
        }
    }
    let mut positions = Vec::new();
    let mut market_value = Decimal::ZERO;
    let mut cost_basis = Decimal::ZERO;
    for p in ledger.positions {
        let price = *quotes.get(&p.symbol).ok_or(QuantError::InsufficientData)?;
        let value = mul(p.quantity, price)?;
        let cost = mul(p.quantity, p.average_cost)?;
        market_value = add(market_value, value)?;
        cost_basis = add(cost_basis, cost)?;
        positions.push(ValuedHolding {
            holding: p,
            market_value: value,
            cost_basis: cost,
            unrealized_gain: add(value, -cost)?,
            weight_percent: Decimal::ZERO,
        });
    }
    if market_value > Decimal::ZERO {
        for p in &mut positions {
            p.weight_percent = mul(div(p.market_value, market_value)?, Decimal::from(100))?;
        }
    }
    let mut sectors = BTreeMap::new();
    for p in &positions {
        let sector = input
            .sectors
            .get(&p.holding.symbol)
            .map_or("Unclassified", String::as_str);
        let total = sectors.entry(sector.to_string()).or_insert(Decimal::ZERO);
        *total = add(*total, p.market_value)?;
    }
    let sectors = sectors
        .into_iter()
        .map(|(name, value)| {
            Ok(SectorAllocation {
                name,
                market_value: value,
                weight_percent: if market_value > Decimal::ZERO {
                    mul(div(value, market_value)?, Decimal::from(100))?
                } else {
                    Decimal::ZERO
                },
            })
        })
        .collect::<Result<Vec<_>>>()?;
    Ok(PortfolioSummary {
        sectors,
        positions,
        market_value,
        cost_basis,
        unrealized_gain: add(market_value, -cost_basis)?,
    })
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct PricePoint {
    #[schemars(with = "String")]
    pub date: NaiveDate,
    #[schemars(with = "String")]
    pub close: Decimal,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct PriceSeries {
    pub symbol: String,
    pub currency: String,
    pub prices: Vec<PricePoint>,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ValuationPoint {
    #[schemars(with = "String")]
    pub date: NaiveDate,
    #[schemars(with = "String")]
    pub market_value: Decimal,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct PerformanceInput {
    pub currency: String,
    pub benchmark: String,
    pub method: String,
    pub positions: Vec<Holding>,
    pub series: Vec<PriceSeries>,
    #[serde(default)]
    pub snapshots: Vec<ValuationPoint>,
    #[serde(default)]
    pub transactions: Vec<PortfolioTrade>,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct PortfolioPerformance {
    pub dates: Vec<String>,
    pub portfolio: Vec<f64>,
    pub benchmark: Vec<f64>,
    pub benchmark_symbol: String,
    pub portfolio_return_percent: f64,
    pub benchmark_return_percent: f64,
    pub method: String,
    pub limitations: String,
}
fn as_float(d: Decimal) -> Result<f64> {
    d.to_string()
        .parse::<f64>()
        .map_err(|_| QuantError::Overflow)
        .and_then(crate::finite)
}
pub fn portfolio_performance(input: &PerformanceInput) -> Result<PortfolioPerformance> {
    if !valid_currency(&input.currency)
        || !valid_symbol(&input.benchmark)
        || input.series.len() > 51
        || input.snapshots.len() > 366
        || input.transactions.len() > 2000
    {
        return Err(QuantError::InvalidInput);
    }
    let mut series = BTreeMap::new();
    for s in &input.series {
        if !valid_symbol(&s.symbol) || s.currency != input.currency || s.prices.len() > 10001 {
            return Err(QuantError::InvalidInput);
        }
        let mut prices = BTreeMap::new();
        let mut previous = None;
        for p in &s.prices {
            if p.close <= Decimal::ZERO
                || p.date > Utc::now().date_naive()
                || previous.is_some_and(|d| p.date <= d)
            {
                return Err(QuantError::InvalidInput);
            }
            previous = Some(p.date);
            prices.insert(p.date, p.close);
        }
        if series.insert(s.symbol.clone(), prices).is_some() {
            return Err(QuantError::InvalidInput);
        }
    }
    let benchmark = series
        .get(&input.benchmark)
        .ok_or(QuantError::InsufficientData)?;
    let mut nav = BTreeMap::new();
    match input.method.as_str() {
        "projection" => {
            let ledger = portfolio_ledger(&LedgerInput {
                opening_positions: input.positions.clone(),
                transactions: vec![],
            })?;
            if ledger.positions.is_empty() {
                return Err(QuantError::InsufficientData);
            }
            let mut common: BTreeSet<_> = benchmark.keys().copied().collect();
            for p in &ledger.positions {
                let prices = series.get(&p.symbol).ok_or(QuantError::InsufficientData)?;
                common.retain(|d| prices.contains_key(d));
            }
            for day in common {
                let mut value = Decimal::ZERO;
                for p in &ledger.positions {
                    value = add(value, mul(p.quantity, series[&p.symbol][&day])?)?;
                }
                nav.insert(day, value);
            }
        }
        "recorded" => {
            let mut previous = None;
            for p in &input.snapshots {
                if p.market_value < Decimal::ZERO
                    || previous.is_some_and(|d| p.date <= d)
                    || p.date > Utc::now().date_naive()
                {
                    return Err(QuantError::InvalidInput);
                }
                previous = Some(p.date);
                if benchmark.contains_key(&p.date) {
                    nav.insert(p.date, p.market_value);
                }
            }
        }
        _ => return Err(QuantError::InvalidInput),
    }
    if nav.len() < 2 {
        return Err(QuantError::InsufficientData);
    }
    let (&first_day, &first_value) = nav.first_key_value().ok_or(QuantError::InsufficientData)?;
    if first_value <= Decimal::ZERO {
        return Err(QuantError::InvalidInput);
    }
    let base = benchmark[&first_day];
    let hundred = Decimal::from(100);
    let mut index = hundred;
    let mut dates = Vec::new();
    let mut portfolios = Vec::new();
    let mut benchmarks = Vec::new();
    let mut previous_day = first_day;
    let mut previous_value = first_value;
    for (&day, &value) in &nav {
        if day != first_day {
            if input.method == "recorded" {
                let mut flow = Decimal::ZERO;
                for t in &input.transactions {
                    if t.date > previous_day && t.date <= day {
                        if t.quantity <= Decimal::ZERO || t.price < Decimal::ZERO {
                            return Err(QuantError::InvalidInput);
                        }
                        let amount = mul(t.quantity, t.price)?;
                        flow = add(flow, if t.side == Side::Buy { amount } else { -amount })?;
                    }
                }
                index = mul(index, div(add(value, -flow)?, previous_value)?)?;
            } else {
                index = mul(div(value, first_value)?, hundred)?;
            }
        }
        dates.push(day.to_string());
        portfolios.push(as_float(index)?);
        benchmarks.push(as_float(mul(div(benchmark[&day], base)?, hundred)?)?);
        previous_day = day;
        previous_value = value;
    }
    Ok(PortfolioPerformance{portfolio_return_percent:portfolios[portfolios.len()-1]-100.0,benchmark_return_percent:benchmarks[benchmarks.len()-1]-100.0,dates,portfolio:portfolios,benchmark:benchmarks,benchmark_symbol:input.benchmark.clone(),method:input.method.clone(),limitations:if input.method=="recorded" {"Recorded valuation snapshots; end-of-period trade-flow adjustment; dividends treated as distributions. Sparse snapshots and intraperiod flows limit accuracy. No cash, fees, taxes or FX."} else {"Fixed-holdings back-projection, not actual trading performance. Adjusted closes; common dates; no cash, fees, taxes or FX."}.into()})
}

#[cfg(test)]
mod tests {
    use super::*;
    fn d(s: &str) -> Decimal {
        s.parse()
            .unwrap_or_else(|_| panic!("invalid fixture decimal"))
    }
    fn trade(id: &str, side: Side, qty: &str, price: &str) -> PortfolioTrade {
        PortfolioTrade {
            id: id.into(),
            symbol: "AAPL".into(),
            side,
            quantity: d(qty),
            price: d(price),
            date: NaiveDate::from_ymd_opt(2025, 1, 1)
                .unwrap_or_else(|| panic!("invalid fixture date")),
            notes: String::new(),
        }
    }
    #[test]
    fn accounting_and_duplicate_ids() {
        let buy = trade("buy", Side::Buy, "10", "200");
        let input = LedgerInput {
            opening_positions: vec![Holding {
                symbol: "AAPL".into(),
                quantity: d("10"),
                average_cost: d("100"),
            }],
            transactions: vec![
                buy.clone(),
                buy,
                trade("sell", Side::Sell, "5", "180"),
                trade("div", Side::Dividend, "15", "0.1"),
            ],
        };
        let out = portfolio_ledger(&input).unwrap_or_else(|_| panic!("invalid fixture accounting"));
        assert_eq!(out.positions[0].quantity, d("15"));
        assert_eq!(out.positions[0].average_cost, d("150"));
        assert_eq!(out.realized_gain, d("150"));
        assert_eq!(out.dividends, d("1.5"));
        assert_eq!(out.transactions.len(), 3);
        let mut conflict = input.clone();
        conflict.transactions[1].price = d("201");
        assert!(portfolio_ledger(&conflict).is_err());
        let mut oversell = input;
        oversell
            .transactions
            .push(trade("bad", Side::Sell, "16", "180"));
        assert!(portfolio_ledger(&oversell).is_err());
    }
    #[test]
    fn decimal_cost_and_full_sale() {
        let out = portfolio_ledger(&LedgerInput {
            opening_positions: vec![],
            transactions: vec![
                trade("b", Side::Buy, "0.3", "0.1"),
                trade("s", Side::Sell, "0.3", "0.2"),
            ],
        })
        .unwrap_or_else(|_| panic!("invalid fixture accounting"));
        assert!(out.positions.is_empty());
        assert_eq!(out.realized_gain, d("0.03"));
    }
    #[test]
    fn no_mixed_currency_or_missing_quotes() {
        let p = Holding {
            symbol: "AAPL".into(),
            quantity: d("2"),
            average_cost: d("10"),
        };
        assert!(portfolio_summary(&SummaryInput {
            sectors: BTreeMap::new(),
            currency: "USD".into(),
            positions: vec![p.clone()],
            quotes: vec![]
        })
        .is_err());
        assert!(portfolio_summary(&SummaryInput {
            sectors: BTreeMap::new(),
            currency: "USD".into(),
            positions: vec![p],
            quotes: vec![PortfolioQuote {
                symbol: "AAPL".into(),
                currency: "MYR".into(),
                price: d("20")
            }]
        })
        .is_err());
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RecoveryInput {
    pub positions: Vec<Holding>,
    pub transactions: Vec<PostedTrade>,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct RecoveryResult {
    pub positions: Vec<Holding>,
}
pub fn recover_opening_positions(input: &RecoveryInput) -> Result<RecoveryResult> {
    let validated = portfolio_ledger(&LedgerInput {
        opening_positions: input.positions.clone(),
        transactions: vec![],
    })?;
    if input.transactions.len() > 2000 {
        return Err(QuantError::InvalidInput);
    }
    let mut positions: BTreeMap<String, Holding> = validated
        .positions
        .into_iter()
        .map(|p| (p.symbol.clone(), p))
        .collect();
    for posted in input.transactions.iter().rev() {
        let t = &posted.trade;
        if t.quantity <= Decimal::ZERO || t.price < Decimal::ZERO || !valid_symbol(&t.symbol) {
            return Err(QuantError::InvalidInput);
        }
        match t.side {
            Side::Buy => {
                let p = positions
                    .get(&t.symbol)
                    .cloned()
                    .ok_or(QuantError::InvalidInput)?;
                let quantity = add(p.quantity, -t.quantity)?;
                if quantity < Decimal::ZERO {
                    return Err(QuantError::InvalidInput);
                }
                if quantity == Decimal::ZERO {
                    positions.remove(&t.symbol);
                } else {
                    let cost = add(mul(p.quantity, p.average_cost)?, -mul(t.quantity, t.price)?)?;
                    if cost < Decimal::ZERO {
                        return Err(QuantError::InvalidInput);
                    }
                    positions.insert(
                        t.symbol.clone(),
                        Holding {
                            symbol: t.symbol.clone(),
                            quantity,
                            average_cost: div(cost, quantity)?,
                        },
                    );
                }
            }
            Side::Sell => {
                let average_cost = add(t.price, -div(posted.realized_gain, t.quantity)?)?;
                if average_cost < Decimal::ZERO {
                    return Err(QuantError::InvalidInput);
                }
                let quantity = add(
                    positions
                        .get(&t.symbol)
                        .map_or(Decimal::ZERO, |p| p.quantity),
                    t.quantity,
                )?;
                positions.insert(
                    t.symbol.clone(),
                    Holding {
                        symbol: t.symbol.clone(),
                        quantity,
                        average_cost,
                    },
                );
            }
            Side::Dividend => {}
        }
    }
    let opening: Vec<_> = positions.into_values().collect();
    let replay = portfolio_ledger(&LedgerInput {
        opening_positions: opening.clone(),
        transactions: input.transactions.iter().map(|t| t.trade.clone()).collect(),
    })?;
    if replay.positions.len() != input.positions.len() {
        return Err(QuantError::InvalidInput);
    }
    let tolerance: Decimal = "0.00000001".parse().map_err(|_| QuantError::InvalidInput)?;
    for actual in &input.positions {
        let p = replay
            .positions
            .iter()
            .find(|p| p.symbol == actual.symbol)
            .ok_or(QuantError::InvalidInput)?;
        if (p.quantity - actual.quantity).abs() > tolerance
            || (p.average_cost - actual.average_cost).abs() > tolerance
        {
            return Err(QuantError::InvalidInput);
        }
    }
    Ok(RecoveryResult { positions: opening })
}
