//! Python-compatible JSON formatting for legacy import IDs and preview receipts.
use super::{invalid, PortfolioError};
use serde_json::value::RawValue;
use std::collections::BTreeMap;

pub(super) fn string(value: &str) -> String {
    let mut result = String::from("\"");
    for unit in value.encode_utf16() {
        match unit {
            34 => result.push_str("\\\""),
            92 => result.push_str("\\\\"),
            8 => result.push_str("\\b"),
            12 => result.push_str("\\f"),
            10 => result.push_str("\\n"),
            13 => result.push_str("\\r"),
            9 => result.push_str("\\t"),
            32..=126 => result.push(char::from(unit as u8)),
            _ => result.push_str(&format!("\\u{unit:04x}")),
        }
    }
    result.push('"');
    result
}
pub(super) fn json(raw: &RawValue) -> Result<String, PortfolioError> {
    value(raw, 0)
}
fn value(raw: &RawValue, depth: usize) -> Result<String, PortfolioError> {
    if depth > 64 {
        return Err(invalid("Import JSON exceeds nesting limit"));
    }
    let text = raw.get().trim();
    match text.as_bytes().first() {
        Some(b'{') => {
            let fields: BTreeMap<String, Box<RawValue>> =
                serde_json::from_str(text).map_err(|_| invalid("Invalid import JSON"))?;
            let entries = fields
                .iter()
                .map(|(key, value)| {
                    Ok(format!(
                        "{}: {}",
                        string(key),
                        self::value(value, depth + 1)?
                    ))
                })
                .collect::<Result<Vec<_>, PortfolioError>>()?;
            Ok(format!("{{{}}}", entries.join(", ")))
        }
        Some(b'[') => {
            let fields: Vec<Box<RawValue>> =
                serde_json::from_str(text).map_err(|_| invalid("Invalid import JSON"))?;
            let values = fields
                .iter()
                .map(|raw| value(raw, depth + 1))
                .collect::<Result<Vec<_>, _>>()?;
            Ok(format!("[{}]", values.join(", ")))
        }
        Some(b'"') => serde_json::from_str::<String>(text)
            .map(|value| string(&value))
            .map_err(|_| invalid("Invalid import text")),
        Some(b't' | b'f' | b'n') => Ok(text.into()),
        Some(_) => {
            if !text.contains(['.', 'e', 'E']) {
                return Ok(if text == "-0" { "0" } else { text }.into());
            }
            float(text)
        }
        None => Err(invalid("Invalid import JSON")),
    }
}
fn float(text: &str) -> Result<String, PortfolioError> {
    let value: f64 = text.parse().map_err(|_| invalid("Invalid import number"))?;
    if !value.is_finite() {
        return Err(invalid("Import numbers must be finite"));
    }
    if value == 0.0 {
        return Ok(if value.is_sign_negative() {
            "-0.0"
        } else {
            "0.0"
        }
        .into());
    }
    let encoded = serde_json::to_string(&value).map_err(|_| invalid("Invalid import number"))?;
    let sign = if value.is_sign_negative() { "-" } else { "" };
    let unsigned = encoded.trim_start_matches('-');
    let (mantissa, power) = match unsigned.split_once('e') {
        Some((mantissa, power)) => (
            mantissa,
            power
                .parse::<i32>()
                .map_err(|_| invalid("Invalid import exponent"))?,
        ),
        None => (unsigned, 0),
    };
    let integer_len = mantissa.split('.').next().map_or(0, str::len) as i32;
    let all = mantissa.replace('.', "");
    let leading = all.bytes().take_while(|value| *value == b'0').count();
    let digits = all.trim_start_matches('0').trim_end_matches('0');
    let exponent = power + integer_len - leading as i32 - 1;
    if !(-4..16).contains(&exponent) {
        let coefficient = if digits.len() == 1 {
            digits.into()
        } else {
            format!("{}.{}", &digits[..1], &digits[1..])
        };
        return Ok(format!(
            "{sign}{coefficient}e{}{power:02}",
            if exponent >= 0 { '+' } else { '-' },
            power = exponent.abs()
        ));
    }
    let point = exponent + 1;
    let decimal = if point <= 0 {
        format!("0.{}{}", "0".repeat((-point) as usize), digits)
    } else if point as usize >= digits.len() {
        format!("{}{}.0", digits, "0".repeat(point as usize - digits.len()))
    } else {
        format!(
            "{}.{}",
            &digits[..point as usize],
            &digits[point as usize..]
        )
    };
    Ok(format!("{sign}{decimal}"))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn python_float_and_unicode_representations() -> Result<(), Box<dyn std::error::Error>> {
        for (raw, expected) in [
            ("-0", "0"),
            ("1e-5", "1e-05"),
            ("1e-4", "0.0001"),
            ("1e16", "1e+16"),
            ("1e15", "1000000000000000.0"),
            ("-0.0", "-0.0"),
            ("1.25", "1.25"),
        ] {
            let raw: Box<RawValue> = serde_json::from_str(raw)?;
            assert_eq!(json(&raw)?, expected);
        }
        assert_eq!(string("é🚀"), "\"\\u00e9\\ud83d\\ude80\"");
        Ok(())
    }
}
