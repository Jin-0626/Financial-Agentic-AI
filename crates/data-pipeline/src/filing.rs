use crate::PipelineError;
use quick_xml::{events::Event, name::ResolveResult, reader::NsReader};
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use std::str::FromStr;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct XbrlFact {
    pub concept: String,
    pub context_ref: String,
    pub unit_ref: String,
    pub value: Decimal,
}
/// Raw standard-taxonomy numeric facts only. No entity/network resolution or inferred contexts.
pub fn extract_xbrl_facts(xml: &[u8]) -> Result<Vec<XbrlFact>, PipelineError> {
    if xml.len() > 2 * 1024 * 1024 {
        return Err(PipelineError::UnsupportedFiling);
    }
    let mut reader = NsReader::from_reader(xml);
    reader.config_mut().trim_text(true);
    let mut depth: u32 = 0;
    let mut facts = Vec::new();
    let mut current: Option<(u32, String, String, String, String)> = None;
    loop {
        let (namespace, event) = reader
            .read_resolved_event()
            .map_err(|_| PipelineError::UnsupportedFiling)?;
        match event {
            Event::DocType(_) | Event::GeneralRef(_) => {
                return Err(PipelineError::UnsupportedFiling)
            }
            Event::Start(element) => {
                depth += 1;
                if depth > 64 || current.is_some() {
                    return Err(PipelineError::UnsupportedFiling);
                }
                let mut context = None;
                let mut unit = None;
                for attribute in element.attributes() {
                    let attribute = attribute.map_err(|_| PipelineError::UnsupportedFiling)?;
                    if attribute.key.as_ref() == b"contextRef" {
                        context = Some(
                            String::from_utf8(attribute.value.to_vec())
                                .map_err(|_| PipelineError::UnsupportedFiling)?,
                        );
                    }
                    if attribute.key.as_ref() == b"unitRef" {
                        unit = Some(
                            String::from_utf8(attribute.value.to_vec())
                                .map_err(|_| PipelineError::UnsupportedFiling)?,
                        );
                    }
                }
                if let (Some(context), Some(unit)) = (context, unit) {
                    let uri = match namespace {
                        ResolveResult::Bound(uri) => String::from_utf8(uri.as_ref().to_vec())
                            .map_err(|_| PipelineError::UnsupportedFiling)?,
                        _ => return Err(PipelineError::UnsupportedFiling),
                    };
                    if !(uri.starts_with("http://fasb.org/us-gaap/")
                        || uri.starts_with("https://xbrl.ifrs.org/taxonomy/"))
                    {
                        return Err(PipelineError::UnsupportedFiling);
                    }
                    let concept = String::from_utf8(element.local_name().as_ref().to_vec())
                        .map_err(|_| PipelineError::UnsupportedFiling)?;
                    current = Some((depth, concept, context, unit, String::new()));
                }
            }
            Event::Text(text) => {
                if let Some((_, _, _, _, value)) = &mut current {
                    value.push_str(
                        &text
                            .decode()
                            .map_err(|_| PipelineError::UnsupportedFiling)?,
                    );
                }
            }
            Event::End(_) => {
                if current.as_ref().is_some_and(|(level, ..)| *level == depth) {
                    let (_, concept, context_ref, unit_ref, value) =
                        current.take().ok_or(PipelineError::UnsupportedFiling)?;
                    facts.push(XbrlFact {
                        concept,
                        context_ref,
                        unit_ref,
                        value: Decimal::from_str(value.trim())
                            .map_err(|_| PipelineError::UnsupportedFiling)?,
                    });
                    if facts.len() > 10_000 {
                        return Err(PipelineError::UnsupportedFiling);
                    }
                }
                depth = depth
                    .checked_sub(1)
                    .ok_or(PipelineError::UnsupportedFiling)?;
            }
            Event::Eof => break,
            _ => {}
        }
    }
    if depth != 0 || facts.is_empty() {
        return Err(PipelineError::UnsupportedFiling);
    }
    Ok(facts)
}
