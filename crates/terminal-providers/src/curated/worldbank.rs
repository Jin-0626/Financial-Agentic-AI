use super::*;

impl CuratedClient {
    async fn wb_observations(
        &self,
        country: &str,
        indicator: &str,
        window: Option<&str>,
    ) -> Result<Value, ProviderError> {
        identifier(indicator)?;
        let url = format!("https://api.worldbank.org/v2/country/{country}/indicator/{indicator}");
        let mut params = vec![("format", "json".into()), ("per_page", "500".into())];
        if let Some(window) = window {
            params.push(("date", window.into()));
        }
        let raw = self.get(&url, &params, CuratedProvider::WorldBank).await?;
        let values = rows(&raw);
        if values.len() != 2
            || !values[0].is_object()
            || !(values[1].is_null() || values[1].is_array())
        {
            return Err(invalid("World Bank did not return indicator observations"));
        }
        let mut metadata = values[0].clone();
        metadata["source"] = json!("World Bank");
        metadata["source_url"] = json!(url);
        metadata["truncated"] = json!(metadata["pages"].as_u64().unwrap_or(1) > 1);
        Ok(json!({"data":rows(&values[1]),"metadata":metadata,"error":null}))
    }
    pub(super) async fn worldbank(
        &self,
        command: &str,
        args: &[String],
    ) -> Result<Value, ProviderError> {
        let country = if command == "commodity_prices" {
            "ALL".into()
        } else {
            args.first()
                .map_or("MYS", String::as_str)
                .to_ascii_uppercase()
                .replace(',', ";")
        };
        if country.len() < 2
            || country.len() > 128
            || !country.bytes().all(|v| v.is_ascii_uppercase() || v == b';')
        {
            return Err(invalid("Invalid country identifier"));
        }
        if command == "economic_snapshot" {
            let mut data = Map::new();
            for (name, code) in [
                ("gdp", "NY.GDP.MKTP.CD"),
                ("gdp_per_capita", "NY.GDP.PCAP.CD"),
                ("growth", "NY.GDP.MKTP.KD.ZG"),
                ("inflation", "FP.CPI.TOTL.ZG"),
                ("unemployment", "SL.UEM.TOTL.ZS"),
            ] {
                data.insert(
                    name.into(),
                    self.wb_observations(&country, code, None).await?,
                );
            }
            return Ok(json!({"country":country,"source":"World Bank","indicators":data}));
        }
        if command == "indicators" {
            let window = args.get(2).map(String::as_str);
            if let Some(window) = window {
                let years: Vec<_> = window.split(':').collect();
                if years.is_empty()
                    || years.len() > 2
                    || years
                        .iter()
                        .any(|v| v.len() != 4 || !v.bytes().all(|b| b.is_ascii_digit()))
                    || years.len() == 2 && years[0] > years[1]
                {
                    return Err(invalid("Indicator window must be YYYY or YYYY:YYYY"));
                }
            }
            return self
                .wb_observations(
                    &country,
                    args.get(1).map_or("NY.GDP.PCAP.CD", String::as_str),
                    window,
                )
                .await;
        }
        let years = bounded(
            args.get(1),
            if command == "gdp_per_capita" { 20 } else { 10 },
        )? as i32;
        let year = Utc::now().year();
        let window = format!("{}:{year}", year - years + 1);
        let code = if command == "commodity_prices" {
            args.first().map_or("CRUDE_PETRO", String::as_str)
        } else {
            "NY.GDP.PCAP.CD"
        };
        self.wb_observations(&country, code, Some(&window)).await
    }
}
