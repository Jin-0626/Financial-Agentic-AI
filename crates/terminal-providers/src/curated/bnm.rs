use super::*;

impl CuratedClient {
    pub(super) async fn bnm(&self, command: &str, args: &[String]) -> Result<Value, ProviderError> {
        let currencies: Vec<_> = CURRENCIES.split_whitespace().collect();
        if command == "available" {
            let mut commands = CuratedProvider::Bnm.commands().to_vec();
            commands.sort_unstable();
            return Ok(
                json!({"success":true,"commands":commands,"all_currencies":currencies,
                "data":{"all_currencies":currencies,"major_currencies":["USD","EUR","GBP","JPY","AUD","CHF","CAD","CNY"]},
                "source":"Bank Negara Malaysia","url":"https://api.bnm.gov.my/public"}),
            );
        }
        if command == "currencies" {
            return Ok(json!({"success":true,"count":currencies.len(),"currencies":currencies}));
        }
        let route = match command {
            "currency" | "fx" | "major" | "asean" => "exchange-rate",
            "opr" => "opr",
            "interest" => "interest-rate",
            "base_rate" => "base-rate",
            "gold" => "kijang-emas",
            _ => return Err(invalid("Unsupported BNM command")),
        };
        let session = match command {
            "currency" => args.get(1),
            "fx" | "major" | "asean" => args.first(),
            _ => None,
        }
        .map_or("1130", String::as_str);
        if !["0900", "1130", "1200", "1700"].contains(&session) {
            return Err(invalid("Unsupported BNM rate session"));
        }
        let mut url = format!("https://api.bnm.gov.my/public/{route}");
        if command == "currency" {
            let currency = args
                .first()
                .ok_or_else(|| invalid("Currency code is required"))?
                .to_ascii_uppercase();
            if currency.len() != 3 || !currency.bytes().all(|v| v.is_ascii_uppercase()) {
                return Err(invalid("Invalid currency code"));
            }
            url.push('/');
            url.push_str(&currency);
        }
        let parameters = if route == "exchange-rate" {
            vec![("session", session.into())]
        } else {
            vec![]
        };
        let raw = self.get(&url, &parameters, CuratedProvider::Bnm).await?;
        if route != "exchange-rate" {
            return Ok(
                json!({"success":true,"data":raw.get("data").cloned().unwrap_or(json!([])),
            "meta":raw.get("meta").cloned().unwrap_or(json!({})),"source":"Bank Negara Malaysia","url":url}),
            );
        }
        let data = raw
            .get("data")
            .ok_or_else(|| invalid("Missing BNM observations"))?;
        let entries = if data.is_array() {
            rows(data).to_vec()
        } else {
            vec![data.clone()]
        };
        let selected = match command {
            "major" => Some("USD EUR GBP JPY AUD CHF CAD CNY"),
            "asean" => Some("SGD THB IDR PHP VND BND MMK KHR LAK"),
            _ => None,
        };
        let mut full = Vec::new();
        let mut flat = Map::new();
        for row in entries {
            let currency = row
                .get("currency_code")
                .or_else(|| row.get("currency"))
                .and_then(Value::as_str)
                .ok_or_else(|| invalid("Missing BNM currency"))?;
            if selected.is_some_and(|set| !set.split_whitespace().any(|v| v == currency)) {
                continue;
            }
            let unit = row
                .get("unit")
                .map(decimal)
                .transpose()?
                .unwrap_or(Decimal::ONE);
            if unit <= Decimal::ZERO {
                return Err(invalid("Provider returned invalid currency units"));
            }
            let rate = &row["rate"];
            let mut record = json!({"currency":currency,"date":rate.get("date").or_else(|| row.get("date")), "unit":unit.to_string()});
            for key in ["buying", "selling", "middle"] {
                record[key] = match rate.get(key).filter(|v| !v.is_null()) {
                    Some(value) => Value::String(
                        decimal(value)?
                            .checked_div(unit)
                            .ok_or_else(|| invalid("Currency rate overflow"))?
                            .to_string(),
                    ),
                    None => Value::Null,
                };
            }
            flat.insert(
                currency.into(),
                if record["middle"].is_null() {
                    record["buying"].clone()
                } else {
                    record["middle"].clone()
                },
            );
            full.push(record);
        }
        Ok(
            json!({"success":true,"session":session,"date":full.first().map_or(json!(""), |r| r["date"].clone()),
            "data":flat,"count":full.len(),"full_data":full,"source":"Bank Negara Malaysia","url":url,
            "meta":raw.get("meta").cloned().unwrap_or(json!({})),"note":"MYR per one foreign currency unit"}),
        )
    }
}
