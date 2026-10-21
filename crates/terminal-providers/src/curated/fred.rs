use super::*;

impl CuratedClient {
    async fn fred_request(
        &self,
        route: &str,
        mut params: Vec<(&str, String)>,
    ) -> Result<Value, ProviderError> {
        let key = self
            .fred_key
            .as_ref()
            .filter(|v| !v.trim().is_empty())
            .ok_or_else(|| invalid("FRED_API_KEY is required"))?;
        params.push(("api_key", key.clone()));
        params.push(("file_type", "json".into()));
        self.get(
            &format!("https://api.stlouisfed.org/fred/{route}"),
            &params,
            CuratedProvider::Fred,
        )
        .await
    }
    pub(super) async fn fred(
        &self,
        command: &str,
        args: &[String],
    ) -> Result<Value, ProviderError> {
        if command == "series" {
            let (date_args, ids): (Vec<_>, Vec<_>) = args.iter().cloned().partition(|v| {
                v.len() == 10
                    && v.as_bytes().get(4) == Some(&b'-')
                    && v.as_bytes().get(7) == Some(&b'-')
            });
            let bounds = date_bounds(&date_args)?;
            if ids.is_empty() || ids.len() > 10 {
                return Err(invalid("Supply 1..10 series identifiers"));
            }
            for id in &ids {
                identifier(id)?;
            }
            let mut output = Map::new();
            for id in &ids {
                let mut params = vec![("series_id", id.clone()), ("limit", "500".into())];
                if let Some(start) = bounds.first() {
                    params.push(("observation_start", start.clone()));
                }
                if let Some(end) = bounds.get(1) {
                    params.push(("observation_end", end.clone()));
                }
                let raw = self.fred_request("series/observations", params).await?;
                let metadata = self
                    .fred_request("series", vec![("series_id", id.clone())])
                    .await?;
                let meta = rows(&metadata["seriess"])
                    .first()
                    .cloned()
                    .unwrap_or(Value::Null);
                let observations = rows(&raw["observations"]);
                output.insert(id.clone(), json!({"series_id":id,"title":meta["title"],"units":meta["units"],"frequency":meta["frequency"],
                    "observations":observations,"count":observations.len(),"last_updated":meta["last_updated"],"source":"FRED",
                    "metadata":{"truncated":raw["count"].as_u64().unwrap_or(observations.len() as u64) > observations.len() as u64}}));
            }
            return if ids.len() == 1 {
                output
                    .remove(&ids[0])
                    .ok_or_else(|| invalid("Missing FRED result"))
            } else {
                Ok(Value::Object(output))
            };
        }
        if command == "search" {
            let query = args
                .first()
                .filter(|v| !v.is_empty() && v.len() <= 200)
                .ok_or_else(|| invalid("Search text of 1..200 characters is required"))?;
            let raw = self
                .fred_request(
                    "series/search",
                    vec![
                        ("search_text", query.clone()),
                        ("limit", bounded(args.get(1), 10)?.to_string()),
                    ],
                )
                .await?;
            return Ok(
                json!({"search_text":query,"count":raw.get("count").cloned().unwrap_or(json!(0)),"series":rows(&raw["seriess"])}),
            );
        }
        let (route, field, result) = match command {
            "categories" => ("category", "category_id", "categories"),
            "category_series" => ("category/series", "category_id", "seriess"),
            "releases" => ("release/dates", "release_id", "release_dates"),
            _ => return Err(invalid("Unsupported FRED command")),
        };
        let id = args.first().map_or(Ok(0_u64), |v| {
            v.parse()
                .map_err(|_| invalid("Provider identifier must be nonnegative"))
        })?;
        let mut raw = self
            .fred_request(
                route,
                vec![
                    (field, id.to_string()),
                    ("limit", bounded(args.get(1), 50)?.to_string()),
                ],
            )
            .await?;
        raw["data"] = raw.get(result).cloned().unwrap_or(json!([]));
        Ok(raw)
    }
}
