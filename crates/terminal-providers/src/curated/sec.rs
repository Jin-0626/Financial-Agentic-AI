use super::*;

impl CuratedClient {
    async fn sec_mapping(
        &self,
        query: Option<&str>,
        by_symbol: bool,
    ) -> Result<Vec<Value>, ProviderError> {
        let query = query
            .map(identifier)
            .transpose()?
            .map(str::to_ascii_uppercase);
        let cik = if by_symbol {
            None
        } else {
            query.as_deref().map(parse_cik).transpose()?
        };
        let raw = self
            .get(
                "https://www.sec.gov/files/company_tickers.json",
                &[],
                CuratedProvider::Sec,
            )
            .await?;
        let records = raw
            .as_object()
            .ok_or_else(|| invalid("SEC mapping must be an object"))?;
        Ok(records
            .values()
            .filter(|row| match query.as_ref() {
                None => true,
                Some(query) if by_symbol => row["ticker"]
                    .as_str()
                    .is_some_and(|v| v.eq_ignore_ascii_case(query)),
                Some(_) => row["cik_str"].as_u64() == cik,
            })
            .cloned()
            .collect())
    }
    pub(super) async fn sec(&self, command: &str, args: &[String]) -> Result<Value, ProviderError> {
        if command == "available_form_types" {
            return Ok(
                json!({"form_types":["10-K","10-Q","8-K","20-F","6-K","S-1","DEF 14A","4","13F-HR"]}),
            );
        }
        if command == "cik_map" || command == "symbol_map" {
            let data = self
                .sec_mapping(args.first().map(String::as_str), command == "cik_map")
                .await?;
            return Ok(json!({"count":data.len(),"data":data}));
        }
        if command == "company_facts" {
            let cik = parse_cik(
                args.first()
                    .ok_or_else(|| invalid("A numeric CIK is required"))?,
            )?;
            return self
                .get(
                    &format!("https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010}.json"),
                    &[],
                    CuratedProvider::Sec,
                )
                .await;
        }
        let cik = match args.get(1).filter(|v| !v.is_empty()) {
            Some(cik) => parse_cik(cik)?,
            None => {
                let mapping = self
                    .sec_mapping(args.first().map(String::as_str), true)
                    .await?;
                if mapping.len() != 1 {
                    return Err(invalid("An exact symbol or CIK is required"));
                }
                mapping[0]["cik_str"]
                    .as_u64()
                    .ok_or_else(|| invalid("Invalid SEC CIK"))?
            }
        };
        let form = args.get(2).map(|v| identifier(v)).transpose()?;
        let bounds = date_bounds(args.get(3..args.len().min(5)).unwrap_or(&[]))?;
        let maximum = bounded(args.get(5), 50)?;
        let raw = self
            .get(
                &format!("https://data.sec.gov/submissions/CIK{cik:010}.json"),
                &[],
                CuratedProvider::Sec,
            )
            .await?;
        let recent = raw["filings"]["recent"]
            .as_object()
            .ok_or_else(|| invalid("SEC recent filings are missing"))?;
        let mut data = Vec::new();
        for (index, filed) in rows(&raw["filings"]["recent"]["filingDate"])
            .iter()
            .enumerate()
        {
            let Some(filed) = filed.as_str() else {
                return Err(invalid("Invalid SEC filing date"));
            };
            if form.is_some_and(|value| {
                raw["filings"]["recent"]["form"]
                    .get(index)
                    .and_then(Value::as_str)
                    != Some(value)
            }) || bounds.first().is_some_and(|v| filed < v.as_str())
                || bounds.get(1).is_some_and(|v| filed > v.as_str())
            {
                continue;
            }
            let mut row: Map<String, Value> = recent
                .iter()
                .filter_map(|(key, values)| {
                    values
                        .as_array()?
                        .get(index)
                        .map(|v| (key.clone(), v.clone()))
                })
                .collect();
            let accession = row
                .get("accessionNumber")
                .and_then(Value::as_str)
                .ok_or_else(|| invalid("Missing SEC accession"))?
                .replace('-', "");
            let document = row
                .get("primaryDocument")
                .and_then(Value::as_str)
                .ok_or_else(|| invalid("Missing SEC document"))?;
            row.insert(
                "url".into(),
                json!(format!(
                    "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
                )),
            );
            data.push(Value::Object(row));
            if data.len() == maximum {
                break;
            }
        }
        Ok(
            json!({"cik":format!("{cik:010}"),"name":raw["name"],"count":data.len(),"data":data,
            "limitations":["Recent submissions only; older filing archive pages are not fetched"]}),
        )
    }
}
