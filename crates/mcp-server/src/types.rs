use data_pipeline::{valid_id, valid_ticker, Dataset, PipelineError, SnapshotStore};
use quant_engine::*;
use rust_decimal::Decimal;
use schemars::{schema_for, JsonSchema};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::str::FromStr;
use tokio_util::sync::CancellationToken;

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DcfArgs {
    pub ticker: String,
    #[schemars(range(min = 0.0, max = 1.0))]
    pub wacc: f64,
    pub terminal_growth_rate: f64,
    #[schemars(range(min = 1, max = 20))]
    pub historical_years: u32,
    pub forecast_id: String,
    pub snapshot_id: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RiskArgs {
    pub ticker: String,
    #[schemars(range(min = 2, max = 10000))]
    pub lookback_days: u32,
    #[schemars(range(min = 0.0, max = 1.0))]
    pub confidence_level: f64,
    pub snapshot_id: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RatioArgs {
    pub ticker: String,
    pub period: String,
    pub snapshot_id: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct SimulationArgs {
    pub ticker: String,
    #[schemars(range(min = 100, max = 100000))]
    pub simulations: u32,
    #[schemars(range(min = 1, max = 2520))]
    pub horizon_days: u32,
    pub seed: u64,
    #[schemars(range(min = 2, max = 10000))]
    pub lookback_days: u32,
    pub snapshot_id: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Provenance {
    pub snapshot_ids: Vec<String>,
    pub ticker: String,
    pub currency: String,
    pub unit_multiplier: String,
    pub as_of: String,
    pub retrieved_at: String,
    pub provider: String,
    pub source_reference: String,
    pub method_version: String,
    pub limitations: Vec<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ToolError {
    pub category: String,
    pub code: String,
    pub message: String,
}
impl ToolError {
    pub fn new(category: &str, code: &str, message: &str) -> Self {
        Self {
            category: category.into(),
            code: code.into(),
            message: message.into(),
        }
    }
}
impl From<PipelineError> for ToolError {
    fn from(error: PipelineError) -> Self {
        match error {
            PipelineError::InvalidData => Self::new(
                "validation",
                "invalid_data",
                "Normalized data or identity is invalid",
            ),
            PipelineError::Unavailable => Self::new(
                "data",
                "snapshot_unavailable",
                "Required snapshot or observation window is unavailable",
            ),
            PipelineError::Corrupt => Self::new(
                "data",
                "snapshot_integrity",
                "Snapshot integrity check failed",
            ),
            PipelineError::UnsupportedFiling => Self::new(
                "validation",
                "unsupported_filing",
                "Filing structure or taxonomy is unsupported",
            ),
            PipelineError::CircuitOpen => {
                Self::new("provider", "circuit_open", "Provider circuit is open")
            }
            PipelineError::Provider => Self::new(
                "provider",
                "provider_unavailable",
                "Provider did not return validated data",
            ),
            PipelineError::Storage => {
                Self::new("runtime", "storage_failed", "Storage operation failed")
            }
        }
    }
}
impl From<QuantError> for ToolError {
    fn from(error: QuantError) -> Self {
        match error {
            QuantError::InvalidInput => Self::new(
                "validation",
                "invalid_input",
                "Calculation inputs are invalid",
            ),
            QuantError::InsufficientData => {
                Self::new("data", "insufficient_data", "Insufficient observations")
            }
            QuantError::Overflow => Self::new(
                "calculation",
                "numeric_overflow",
                "Calculation exceeds finite numeric range",
            ),
            QuantError::Cancelled => Self::new("runtime", "cancelled", "Calculation cancelled"),
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "status", rename_all = "snake_case", deny_unknown_fields)]
pub enum Envelope<T> {
    Success { data: T, provenance: Provenance },
    Error { error: ToolError },
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RiskAnalysis {
    pub historical: RiskResult,
    pub parametric: RiskResult,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct MonteCarloAnalysis {
    pub summary: MonteCarloResult,
    pub artifact_id: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkerJob {
    pub name: String,
    pub arguments: Value,
    pub snapshot_id: String,
    pub traceparent: Option<String>,
    pub tracestate: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkerReply {
    pub result: Value,
    pub is_error: bool,
}
fn invalid() -> ToolError {
    ToolError::new(
        "validation",
        "invalid_arguments",
        "Tool arguments do not match the declared schema",
    )
}
fn parse<T: serde::de::DeserializeOwned>(value: &Value) -> Result<T, ToolError> {
    serde_json::from_value(value.clone()).map_err(|_| invalid())
}
pub fn schemas() -> Value {
    json!({"snapshot":schema_for!(data_pipeline::Snapshot), "tools":tools()})
}
pub fn tools() -> Value {
    json!([
        {"name":"compute_discounted_cash_flow", "description":"DCF from an explicitly registered dated forecast and historical statements", "inputSchema":schema_for!(DcfArgs), "outputSchema":schema_for!(Envelope<DcfResult>), "annotations":{"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true}},
        {"name":"calculate_historical_var", "description":"One-day historical and normal-parametric VaR/ES from adjusted daily prices", "inputSchema":schema_for!(RiskArgs), "outputSchema":schema_for!(Envelope<RiskAnalysis>), "annotations":{"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true}},
        {"name":"extract_financial_ratios", "description":"Ratios for an exact reporting-period end date; missing inputs remain unavailable", "inputSchema":schema_for!(RatioArgs), "outputSchema":schema_for!(Envelope<RatioResult>), "annotations":{"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true}},
        {"name":"run_monte_carlo_simulation", "description":"Reproducible seeded GBM terminal scenarios, not price predictions", "inputSchema":schema_for!(SimulationArgs), "outputSchema":schema_for!(Envelope<MonteCarloAnalysis>), "annotations":{"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true}}
    ])
}
pub fn prepare(store: &SnapshotStore, name: &str, args: &Value) -> Result<WorkerJob, ToolError> {
    let (ticker, kind, explicit) = match name {
        "compute_discounted_cash_flow" => {
            let input: DcfArgs = parse(args)?;
            if !(1..=20).contains(&input.historical_years)
                || !input.wacc.is_finite()
                || !input.terminal_growth_rate.is_finite()
                || input.wacc <= 0.0
                || input.wacc > 1.0
                || input.terminal_growth_rate <= -1.0
                || input.wacc <= input.terminal_growth_rate
                || !valid_id(&input.forecast_id)
            {
                return Err(invalid());
            }
            (input.ticker, "statements", input.snapshot_id)
        }
        "calculate_historical_var" => {
            let input: RiskArgs = parse(args)?;
            if !(2..=10_000).contains(&input.lookback_days)
                || !input.confidence_level.is_finite()
                || input.confidence_level <= 0.0
                || input.confidence_level >= 1.0
            {
                return Err(invalid());
            }
            (input.ticker, "prices", input.snapshot_id)
        }
        "extract_financial_ratios" => {
            let input: RatioArgs = parse(args)?;
            if chrono::NaiveDate::parse_from_str(&input.period, "%Y-%m-%d").is_err() {
                return Err(invalid());
            }
            (input.ticker, "statements", input.snapshot_id)
        }
        "run_monte_carlo_simulation" => {
            let input: SimulationArgs = parse(args)?;
            if !(100..=100_000).contains(&input.simulations)
                || !(1..=2520).contains(&input.horizon_days)
                || !(2..=10_000).contains(&input.lookback_days)
            {
                return Err(invalid());
            }
            (input.ticker, "prices", input.snapshot_id)
        }
        _ => return Err(invalid()),
    };
    if !valid_ticker(&ticker) {
        return Err(invalid());
    }
    let snapshot_id = match explicit {
        Some(id) if valid_id(&id) => id,
        Some(_) => return Err(invalid()),
        None => store.latest(&ticker, kind)?,
    };
    Ok(WorkerJob {
        name: name.into(),
        arguments: args.clone(),
        snapshot_id,
        traceparent: None,
        tracestate: None,
    })
}
fn provenance(snapshot: &data_pipeline::Snapshot, ids: Vec<String>) -> Provenance {
    Provenance {
        snapshot_ids: ids,
        ticker: snapshot.ticker.clone(),
        currency: snapshot.currency.clone(),
        unit_multiplier: snapshot.unit_multiplier.to_string(),
        as_of: snapshot.as_of.to_string(),
        retrieved_at: snapshot.retrieved_at.to_rfc3339(),
        provider: snapshot.provider.clone(),
        source_reference: snapshot.source_reference.clone(),
        method_version: METHOD_VERSION.into(),
        limitations: vec![
            "Provider/snapshot provenance is reported, not independently audited".into(),
        ],
    }
}
fn encode<T: Serialize>(data: T, provenance: Provenance) -> Result<Value, ToolError> {
    serde_json::to_value(Envelope::Success { data, provenance })
        .map_err(|_| ToolError::from(QuantError::Overflow))
}
pub fn compute(
    store: &SnapshotStore,
    job: &WorkerJob,
    cancel: &CancellationToken,
) -> Result<Value, ToolError> {
    match job.name.as_str() {
        "compute_discounted_cash_flow" => {
            let input: DcfArgs = parse(&job.arguments)?;
            let historical = store.load(&job.snapshot_id, &input.ticker, "statements")?;
            let forecast = store.load(&input.forecast_id, &input.ticker, "forecast")?;
            let Dataset::Statements { periods } = &historical.dataset else {
                return Err(invalid());
            };
            let Dataset::Forecast { forecast: model } = &forecast.dataset else {
                return Err(invalid());
            };
            if historical.currency != forecast.currency
                || historical.as_of > model.valuation_date
                || periods
                    .iter()
                    .filter(|p| p.annual && p.period <= model.valuation_date)
                    .count()
                    < input.historical_years as usize
            {
                return Err(PipelineError::Unavailable.into());
            }
            let rate = |value: f64| Decimal::from_str(&value.to_string()).map_err(|_| invalid());
            let result = discounted_cash_flow(&DcfInput {
                forecast: model.clone(),
                wacc: rate(input.wacc)?,
                terminal_growth_rate: rate(input.terminal_growth_rate)?,
            })?;
            let mut metadata =
                provenance(&forecast, vec![job.snapshot_id.clone(), input.forecast_id]);
            metadata.limitations.push("Forecast supplied explicitly; historical data does not establish future cash flows".into());
            encode(result, metadata)
        }
        "calculate_historical_var" => {
            let input: RiskArgs = parse(&job.arguments)?;
            let (snapshot, returns, _, _) =
                store.returns(&job.snapshot_id, &input.ticker, input.lookback_days)?;
            let result = RiskAnalysis {
                historical: historical_var(&returns, input.confidence_level)?,
                parametric: parametric_var(&returns, input.confidence_level)?,
            };
            let mut metadata = provenance(&snapshot, vec![job.snapshot_id.clone()]);
            metadata.limitations.push("One-day risk estimate; sparse tails and illiquidity can invalidate model assumptions; no liquidity adjustment".into());
            encode(result, metadata)
        }
        "extract_financial_ratios" => {
            let input: RatioArgs = parse(&job.arguments)?;
            let snapshot = store.load(&job.snapshot_id, &input.ticker, "statements")?;
            let Dataset::Statements { periods } = &snapshot.dataset else {
                return Err(invalid());
            };
            let period = periods
                .iter()
                .find(|period| period.period.to_string() == input.period)
                .ok_or(PipelineError::Unavailable)?;
            encode(
                financial_ratios(period)?,
                provenance(&snapshot, vec![job.snapshot_id.clone()]),
            )
        }
        "run_monte_carlo_simulation" => {
            let input: SimulationArgs = parse(&job.arguments)?;
            let (snapshot, _, log_returns, spot) =
                store.returns(&job.snapshot_id, &input.ticker, input.lookback_days)?;
            let result = monte_carlo(
                &MonteCarloInput {
                    spot,
                    log_returns,
                    simulations: input.simulations,
                    horizon_days: input.horizon_days,
                    seed: input.seed,
                },
                cancel,
            )?;
            let mut metadata = provenance(&snapshot, vec![job.snapshot_id.clone()]);
            metadata.limitations.push(
                "GBM scenario ignores jumps, regime changes, and liquidity; not a forecast".into(),
            );
            let artifact_id =
                store.commit_artifact(&json!({"summary":result,"provenance":metadata}))?;
            encode(
                MonteCarloAnalysis {
                    summary: result,
                    artifact_id,
                },
                metadata,
            )
        }
        _ => Err(invalid()),
    }
}
pub fn error_reply(error: ToolError) -> WorkerReply {
    WorkerReply {
        result: json!({"status":"error", "error":error}),
        is_error: true,
    }
}
pub fn validate_output(name: &str, value: &Value) -> bool {
    let list = tools();
    let Some(tool) = list
        .as_array()
        .and_then(|tools| tools.iter().find(|tool| tool["name"] == name))
    else {
        return false;
    };
    match jsonschema::validator_for(&tool["outputSchema"]) {
        Ok(validator) => validator.is_valid(value),
        Err(_) => false,
    }
}
