use super::*;

impl CuratedClient {
    pub(super) async fn treasury(
        &self,
        command: &str,
        args: &[String],
    ) -> Result<Value, ProviderError> {
        let route = match command {
            "debt" | "record_debt" => "v2/accounting/od/debt_to_penny",
            "interest_rates" | "avg_rates" => "v2/accounting/od/avg_interest_rates",
            "exchange_rates" => "v1/accounting/od/rates_of_exchange",
            _ => return Err(invalid("Unsupported Treasury command")),
        };
        let mut filters = Vec::new();
        let bounds = if command == "interest_rates" {
            let security = args.first().map_or("Treasury Bills", String::as_str);
            if security.is_empty()
                || security.len() > 60
                || !security
                    .bytes()
                    .all(|v| v.is_ascii_alphabetic() || b" -".contains(&v))
            {
                return Err(invalid("Invalid security type"));
            }
            filters.push(format!("security_desc:eq:{security}"));
            date_bounds(args.get(1..).unwrap_or(&[]))?
        } else {
            date_bounds(args)?
        };
        if let Some(start) = bounds.first() {
            filters.push(format!("record_date:gte:{start}"));
        }
        if let Some(end) = bounds.get(1) {
            filters.push(format!("record_date:lte:{end}"));
        }
        let mut params = vec![
            ("page[size]", "500".into()),
            ("sort", "-record_date".into()),
        ];
        if !filters.is_empty() {
            params.push(("filter", filters.join(",")));
        }
        self.get(
            &format!("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/{route}"),
            &params,
            CuratedProvider::Treasury,
        )
        .await
    }
}
