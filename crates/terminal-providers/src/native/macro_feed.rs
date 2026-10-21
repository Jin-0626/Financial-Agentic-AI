//! Direct macro snapshots: source observations remain dated and nullable.
use super::*;

const INDICES: [(&str, &str); 5] = [
    ("VIX", "^VIX"),
    ("S&P 500", "^GSPC"),
    ("Bursa KLCI", "^KLSE"),
    ("Gold proxy", "GLD"),
    ("Oil proxy", "USO"),
];
const YIELDS: [(&str, &str); 8] = [
    ("1M", "DGS1MO"),
    ("3M", "DGS3MO"),
    ("6M", "DGS6MO"),
    ("1Y", "DGS1"),
    ("2Y", "DGS2"),
    ("5Y", "DGS5"),
    ("10Y", "DGS10"),
    ("30Y", "DGS30"),
];
const NO_FRED: &str =
    "Treasury yield curve requires FRED_API_KEY; fiscal interest records are not yield quotes";

impl NativeMarketProvider {
    pub(super) async fn fred_observations(
        &self,
        series: &str,
        vintage: Option<&str>,
    ) -> Result<Value, ProviderError> {
        let key = self
            .fred_key
            .as_ref()
            .ok_or_else(|| payload("FRED_API_KEY is required for official release observations"))?;
        let mut params = vec![
            ("series_id", series.into()),
            ("api_key", key.clone()),
            ("file_type", "json".into()),
            ("sort_order", "desc".into()),
            ("limit", "24".into()),
        ];
        if let Some(vintage) = vintage {
            params.push(("realtime_start", vintage.into()));
            params.push(("realtime_end", vintage.into()));
        }
        let raw = self
            .get(
                "https://api.stlouisfed.org/fred/series/observations",
                &params,
            )
            .await?;
        let observations = raw["observations"]
            .as_array()
            .ok_or_else(|| payload("Invalid FRED observations"))?;
        let mut rows = Vec::new();
        for row in observations.iter().take(24) {
            let date = row["date"]
                .as_str()
                .filter(|v| v.len() == 10)
                .ok_or_else(|| payload("Invalid FRED date"))?;
            NaiveDate::parse_from_str(date, "%Y-%m-%d")
                .map_err(|_| payload("Invalid FRED date"))?;
            let value = row["value"]
                .as_str()
                .filter(|v| v.len() <= 64)
                .ok_or_else(|| payload("Invalid FRED value"))?;
            if value != "." && decimal(&row["value"]).is_none() {
                return Err(payload("Invalid FRED value"));
            }
            rows.push(json!({"date":date,"value":value}));
        }
        Ok(json!(rows))
    }
    pub async fn macro_value(&self) -> Result<Value, ProviderError> {
        let mut tasks = tokio::task::JoinSet::new();
        for (index, (name, ticker)) in INDICES.into_iter().enumerate() {
            let provider = self.clone();
            tasks.spawn(async move {
                let result = async {
                    let id = InstrumentId::parse(ticker)?;
                    let quote = provider.quote_value(&id).await?;
                    let mut row = quote["data"].clone();
                    let object = row
                        .as_object_mut()
                        .ok_or_else(|| payload("Invalid macro quote"))?;
                    object.insert("name".into(), json!(name));
                    object.insert(
                        "kind".into(),
                        json!(if ticker.starts_with('^') {
                            "index"
                        } else {
                            "ETF proxy"
                        }),
                    );
                    object.insert("source".into(), quote["source"].clone());
                    object.insert("observed_at".into(), quote["observed_at"].clone());
                    Ok(row)
                }
                .await;
                (index, result)
            });
        }
        if self.fred_key.is_some() {
            for (offset, (maturity, series)) in YIELDS.into_iter().enumerate() {
                let provider = self.clone();
                tasks.spawn(async move {
                    let result = async {
                        let rows = provider.fred_observations(series, None).await?;
                        yield_row(&rows, maturity, series)
                    }
                    .await;
                    (5 + offset, result)
                });
            }
        }
        let provider = self.clone();
        tasks.spawn(async move {
            let result = async {
                let request = provider
                    .client
                    .get("https://api.bnm.gov.my/public/opr")
                    .header(header::ACCEPT, "application/vnd.BNM.API.v1+json");
                let (response, _slot) = provider.request(request).await?;
                let raw = crate::native_decimal::decode(&provider.body(response).await?)?;
                opr_row(&raw)
            }
            .await;
            (13, result)
        });
        let mut results = BTreeMap::new();
        while let Some(result) = tasks.join_next().await {
            let (index, result) = result.map_err(|_| payload("Macro provider task stopped"))?;
            results.insert(index, result);
        }
        let mut rows = Vec::new();
        let mut limitations = Vec::new();
        let mut retry_after = None;
        for (index, result) in results {
            if index == 13 && self.fred_key.is_none() {
                limitations.push(NO_FRED.to_owned());
            }
            match result {
                Ok(row) if !row.is_null() => rows.push(row),
                Ok(_) => {}
                Err(error) => {
                    retry_after = retry_after.max(error.retry_after());
                    limitations.push(if index < 5 {
                        format!("{} unavailable", INDICES[index].0)
                    } else if index < 13 {
                        format!("Yield {} unavailable", YIELDS[index - 5].0)
                    } else {
                        "BNM OPR unavailable".into()
                    });
                }
            }
        }
        if rows.is_empty() {
            return Err(ProviderError::Upstream {
                message: "Macro providers temporarily unavailable".into(),
                retry_after,
            });
        }
        let mut output = envelope(
            json!(rows),
            "Yahoo Finance / FRED / BNM",
            "Dated index snapshots, ETF proxies and official daily/policy observations",
        );
        output["limitations"] = json!(limitations);
        Ok(output)
    }
}
fn yield_row(rows: &Value, maturity: &str, series: &str) -> Result<Value, ProviderError> {
    let rows = rows
        .as_array()
        .ok_or_else(|| payload("Invalid FRED observations"))?;
    let Some(row) = rows.iter().find(|row| row["value"] != ".") else {
        return Ok(Value::Null);
    };
    let price = decimal(&row["value"]).ok_or_else(|| payload("Invalid FRED yield"))?;
    let date = row["date"]
        .as_str()
        .ok_or_else(|| payload("Missing FRED date"))?;
    NaiveDate::parse_from_str(date, "%Y-%m-%d").map_err(|_| payload("Invalid FRED date"))?;
    Ok(
        json!({"name":format!("Treasury {maturity}"),"kind":"yield","price":price,"unit":"percent","observed_at":date,"source":"FRED / US Treasury","series_id":series}),
    )
}
fn opr_row(raw: &Value) -> Result<Value, ProviderError> {
    let values = match &raw["data"] {
        Value::Array(rows) => rows.as_slice(),
        Value::Object(_) => std::slice::from_ref(&raw["data"]),
        _ => return Err(payload("Invalid BNM OPR response")),
    };
    let Some(row) = values
        .iter()
        .filter(|row| row.is_object())
        .max_by_key(|row| row["date"].as_str().unwrap_or(""))
    else {
        return Ok(Value::Null);
    };
    let rate = row
        .get("rate")
        .or_else(|| row.get("opr"))
        .unwrap_or(&Value::Null);
    let price = if rate.is_null() {
        None
    } else {
        Some(decimal(rate).ok_or_else(|| payload("Invalid BNM OPR rate"))?)
    };
    Ok(
        json!({"name":"Malaysia OPR","kind":"policy rate","price":price,"unit":"percent","observed_at":row.get("date"),"source":"Bank Negara Malaysia"}),
    )
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn dated_macro_observations_preserve_missing_and_exact_values(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let value = yield_row(
            &json!([{"date":"2026-10-19","value":"."},{"date":"2026-10-16","value":"4.250000000000000001"}]),
            "10Y",
            "DGS10",
        )?;
        assert_eq!(value["price"], "4.250000000000000001");
        assert_eq!(value["observed_at"], "2026-10-16");
        assert!(yield_row(&json!([]), "10Y", "DGS10")?.is_null());
        assert!(yield_row(&json!([{"date":"bad","value":"NaN"}]), "10Y", "DGS10").is_err());
        let opr = opr_row(
            &json!({"data":[{"date":"2026-09-01","rate":"2.75"},{"date":"2026-01-01","opr":"3.00"}]}),
        )?;
        assert_eq!(opr["price"], "2.75");
        assert_eq!(opr["observed_at"], "2026-09-01");
        assert!(opr_row(&json!({"data":[]}))?.is_null());
        assert!(opr_row(&json!({"data":{"date":"2026-09-01"}}))?["price"].is_null());
        Ok(())
    }
}
