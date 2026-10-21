//! Incremental bounded SSE decoding for explicitly submitted research text.
use crate::{DirectHttpProvider, ProviderError};
use serde::{Deserialize, Serialize};
use std::time::Duration;
use tokio::sync::mpsc;
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ResearchRequest {
    pub message: String,
    pub thread_id: String,
}
impl ResearchRequest {
    pub fn validate(&self) -> Result<(), ProviderError> {
        if self.message.trim().is_empty()
            || self.message.len() > 32000
            || self.thread_id.is_empty()
            || self.thread_id.len() > 254
            || self.thread_id.chars().any(char::is_control)
        {
            return Err(ProviderError::Configuration(
                "Invalid research request".into(),
            ));
        }
        Ok(())
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ResearchEvent {
    Token {
        content: String,
    },
    ReasoningToken {
        content: String,
    },
    ToolCall {
        id: String,
        name: String,
        agent: Option<String>,
    },
    ToolResult {
        id: String,
        name: Option<String>,
        status: Option<String>,
        output: Option<String>,
    },
    Budget {},
    BudgetStop {
        message: String,
    },
    Done {
        result: String,
        reasoning: Option<String>,
    },
    Error {
        message: String,
    },
    Interrupted {},
    FinancialStatements {},
}
impl DirectHttpProvider {
    pub async fn research(
        &self,
        input: &ResearchRequest,
        events: mpsc::Sender<ResearchEvent>,
    ) -> Result<(), ProviderError> {
        input.validate()?;
        let url = self
            .base
            .join("api/chat/stream")
            .map_err(|e| ProviderError::Configuration(e.to_string()))?;
        let mut request = self
            .client
            .post(url)
            .json(input)
            .timeout(Duration::from_secs(900));
        if let Some(token) = &self.token {
            request = request.bearer_auth(token.as_ref());
        }
        let mut response = request.send().await?;
        if !response.status().is_success() {
            return Err(ProviderError::Http {
                status: response.status(),
                retry_after: None,
            });
        }
        if !response
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .is_some_and(|value| value.starts_with("text/event-stream"))
        {
            return Err(ProviderError::Payload(
                "Research endpoint did not return SSE".into(),
            ));
        }
        let mut decoder = SseDecoder::default();
        let mut done = false;
        while let Some(chunk) = response.chunk().await? {
            for event in decoder.push(&chunk)? {
                done |= matches!(
                    event,
                    ResearchEvent::Done { .. }
                        | ResearchEvent::Error { .. }
                        | ResearchEvent::Interrupted { .. }
                );
                events
                    .send(event)
                    .await
                    .map_err(|_| ProviderError::Payload("Research receiver closed".into()))?;
            }
        }
        if !done {
            return Err(ProviderError::Payload(
                "Research stream ended before a final response".into(),
            ));
        }
        Ok(())
    }
}
#[derive(Default)]
struct SseDecoder {
    buffer: Vec<u8>,
}
impl SseDecoder {
    fn push(&mut self, bytes: &[u8]) -> Result<Vec<ResearchEvent>, ProviderError> {
        self.buffer.extend_from_slice(bytes);
        if self.buffer.len() > 2 * 1024 * 1024 {
            return Err(ProviderError::Oversized);
        }
        let mut events = Vec::new();
        while let Some((at, width)) = [
            self.buffer
                .windows(2)
                .position(|v| v == b"\n\n")
                .map(|at| (at, 2)),
            self.buffer
                .windows(4)
                .position(|v| v == b"\r\n\r\n")
                .map(|at| (at, 4)),
        ]
        .into_iter()
        .flatten()
        .min_by_key(|(at, _)| *at)
        {
            let frame = std::str::from_utf8(&self.buffer[..at])
                .map_err(|_| ProviderError::Payload("Invalid SSE UTF-8".into()))?;
            let data = frame
                .lines()
                .filter_map(|line| line.strip_prefix("data:"))
                .map(|line| line.strip_prefix(' ').unwrap_or(line))
                .collect::<Vec<_>>()
                .join("\n");
            if !data.is_empty() {
                events.push(serde_json::from_str(&data)?);
            }
            self.buffer.drain(..at + width);
        }
        Ok(events)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn chunked_unicode_multiline_crlf() -> Result<(), ProviderError> {
        let bytes = "data: {\"type\":\"token\",\r\ndata: \"content\":\"世界\"}\r\n\r\n".as_bytes();
        let mut decoder = SseDecoder::default();
        let mut output = Vec::new();
        for byte in bytes {
            output.extend(decoder.push(&[*byte])?);
        }
        assert!(matches!(&output[0], ResearchEvent::Token { content } if content == "世界"));
        Ok(())
    }
    #[test]
    fn mixed_separators_preserve_event_order_at_every_chunk_boundary() -> Result<(), ProviderError>
    {
        let bytes = b"data: {\"type\":\"token\",\"content\":\"first\"}\r\n\r\ndata: {\"type\":\"token\",\"content\":\"second\"}\n\n";
        for split in 0..=bytes.len() {
            let mut decoder = SseDecoder::default();
            let mut events = decoder.push(&bytes[..split])?;
            events.extend(decoder.push(&bytes[split..])?);
            assert_eq!(events.len(), 2);
            assert!(matches!(&events[0], ResearchEvent::Token { content } if content == "first"));
            assert!(matches!(&events[1], ResearchEvent::Token { content } if content == "second"));
        }
        Ok(())
    }
}
