use super::*;

impl NativeMarketProvider {
    pub(super) async fn body(
        &self,
        mut response: reqwest::Response,
    ) -> Result<Vec<u8>, ProviderError> {
        if response
            .content_length()
            .is_some_and(|v| v > MAX_BODY as u64)
        {
            return Err(ProviderError::Oversized);
        }
        let mut bytes = Vec::new();
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|e| ProviderError::Transport(e.without_url()))?
        {
            if bytes.len().saturating_add(chunk.len()) > MAX_BODY {
                return Err(ProviderError::Oversized);
            }
            bytes.extend_from_slice(&chunk);
        }
        Ok(bytes)
    }
    pub(super) async fn request(
        &self,
        request: reqwest::RequestBuilder,
    ) -> Result<(reqwest::Response, OwnedSemaphorePermit), ProviderError> {
        self.request_status(request, false).await
    }
    async fn request_status(
        &self,
        request: reqwest::RequestBuilder,
        accept_session_not_found: bool,
    ) -> Result<(reqwest::Response, OwnedSemaphorePermit), ProviderError> {
        let built = request
            .build()
            .map_err(|e| ProviderError::Transport(e.without_url()))?;
        let host = built
            .url()
            .host_str()
            .ok_or_else(|| payload("Missing provider host"))?
            .to_owned();
        let slot = self
            .slots
            .clone()
            .acquire_owned()
            .await
            .map_err(|_| payload("Market provider stopped"))?;
        if let Some(until) = self.cooldown.lock().await.get(&host) {
            if *until > Instant::now() {
                return Err(ProviderError::Upstream {
                    message: "Provider rate limit reached".into(),
                    retry_after: Some(
                        until
                            .saturating_duration_since(Instant::now())
                            .as_secs()
                            .max(1),
                    ),
                });
            }
        }
        // Test builds can route fixed provider requests into an isolated loopback fixture.
        // This field and override do not exist in production builds.
        #[cfg(test)]
        let built = if let Some(origin) = &self.fixture_origin {
            let mut destination = origin.clone();
            destination.set_path(&format!("/{host}{}", built.url().path()));
            destination.set_query(built.url().query());
            let mut request = built;
            *request.url_mut() = destination;
            request
        } else {
            built
        };
        let response = self
            .client
            .execute(built)
            .await
            .map_err(|e| ProviderError::Transport(e.without_url()))?;
        if !(response.status().is_success()
            || accept_session_not_found && response.status() == StatusCode::NOT_FOUND)
        {
            let retry = response
                .headers()
                .get(header::RETRY_AFTER)
                .and_then(|v| v.to_str().ok())
                .and_then(|value| {
                    value.parse::<u64>().ok().or_else(|| {
                        DateTime::parse_from_rfc2822(value)
                            .ok()
                            .map(|v| (v.timestamp() - Utc::now().timestamp()).max(1) as u64)
                    })
                });
            let retry = if response.status() == StatusCode::TOO_MANY_REQUESTS {
                Some(retry.unwrap_or(60).clamp(1, 300))
            } else {
                retry
            };
            if response.status() == StatusCode::TOO_MANY_REQUESTS {
                self.cooldown.lock().await.insert(
                    host,
                    Instant::now() + Duration::from_secs(retry.unwrap_or(60).clamp(1, 300)),
                );
            }
            return Err(ProviderError::Http {
                status: response.status(),
                retry_after: retry,
            });
        }
        Ok((response, slot))
    }
    pub(super) async fn get(
        &self,
        url: &str,
        params: &[(&str, String)],
    ) -> Result<Value, ProviderError> {
        let mut request = self.client.get(url).query(params);
        if url.starts_with("https://api.coingecko.com/") {
            if let Some(key) = &self.crypto_key {
                request = request.header("x-cg-demo-api-key", key);
            }
        }
        let (response, _slot) = self.request(request).await?;
        crate::native_decimal::decode(&self.body(response).await?)
    }
    pub(super) async fn yahoo_authenticated(
        &self,
        path: &str,
        params: &[(&str, String)],
    ) -> Result<Value, ProviderError> {
        for attempt in 0..2 {
            let (cookie, crumb) = {
                let mut session = self.session.lock().await;
                if session.crumb.is_empty() {
                    // fc.yahoo.com intentionally answers 404 while setting the public A3 cookie.
                    let (response, cookie_slot) = self
                        .request_status(self.client.get("https://fc.yahoo.com"), true)
                        .await?;
                    if response.status().is_redirection() {
                        return Err(payload(
                            "Yahoo session redirected; consent requires manual review",
                        ));
                    }
                    let cookie = response
                        .headers()
                        .get_all(header::SET_COOKIE)
                        .iter()
                        .filter_map(|v| v.to_str().ok())
                        .filter_map(|v| v.split(';').next())
                        .filter(|v| v.starts_with("A3=") || v.starts_with("A1="))
                        .collect::<Vec<_>>()
                        .join("; ");
                    drop(response);
                    drop(cookie_slot);
                    if cookie.is_empty() || cookie.len() > 4096 {
                        return Err(payload("Yahoo public session cookie unavailable"));
                    }
                    let (response, _slot) = self
                        .request(
                            self.client
                                .get("https://query1.finance.yahoo.com/v1/test/getcrumb")
                                .header(header::COOKIE, &cookie),
                        )
                        .await?;
                    let crumb = String::from_utf8(self.body(response).await?)
                        .map_err(|_| payload("Invalid Yahoo session response"))?;
                    if crumb.is_empty()
                        || crumb.len() > 256
                        || crumb.chars().any(char::is_control)
                        || crumb.contains('<')
                    {
                        return Err(payload("Invalid Yahoo session crumb"));
                    }
                    *session = YahooSession { cookie, crumb };
                }
                (session.cookie.clone(), session.crumb.clone())
            };
            let mut query = params.to_vec();
            query.push(("crumb", crumb));
            let response = self
                .request(
                    self.client
                        .get(format!("https://query1.finance.yahoo.com{path}"))
                        .query(&query)
                        .header(header::COOKIE, &cookie),
                )
                .await;
            match response {
                Ok((response, _slot)) => {
                    return crate::native_decimal::decode(&self.body(response).await?)
                }
                Err(error) if attempt == 0 && error.authentication_failure() => {
                    *self.session.lock().await = YahooSession::default();
                }
                Err(error) => return Err(error),
            }
        }
        Err(payload("Yahoo session recovery failed"))
    }
    pub(super) async fn chart(
        &self,
        id: &InstrumentId,
        range: HistoryRange,
    ) -> Result<Value, ProviderError> {
        let (period, interval) = match range {
            HistoryRange::Day => ("1d", "5m"),
            HistoryRange::FiveDays => ("5d", "15m"),
            HistoryRange::Month => ("1mo", "1d"),
            HistoryRange::SixMonths => ("6mo", "1d"),
            HistoryRange::YearToDate => ("ytd", "1d"),
            HistoryRange::Year => ("1y", "1d"),
            HistoryRange::FiveYears => ("5y", "1wk"),
            HistoryRange::Max => ("max", "1mo"),
        };
        let raw = self
            .get(
                &format!(
                    "https://query1.finance.yahoo.com/v8/finance/chart/{}",
                    id.display_symbol()
                ),
                &[
                    ("range", period.into()),
                    ("interval", interval.into()),
                    ("includeAdjustedClose", "true".into()),
                ],
            )
            .await?;
        let row = array(&raw["chart"]["result"])
            .first()
            .ok_or_else(|| payload("Yahoo chart unavailable"))?
            .clone();
        if row["meta"]["symbol"]
            .as_str()
            .is_none_or(|v| !v.eq_ignore_ascii_case(id.display_symbol()))
        {
            return Err(payload("Yahoo chart identity mismatch"));
        }
        Ok(row)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    fn server(
        response: String,
    ) -> Result<(String, std::thread::JoinHandle<std::io::Result<()>>), std::io::Error> {
        let listener = std::net::TcpListener::bind("127.0.0.1:0")?;
        let url = format!("http://{}", listener.local_addr()?);
        let task = std::thread::spawn(move || {
            let (mut socket, _) = listener.accept()?;
            socket.set_read_timeout(Some(Duration::from_secs(2)))?;
            let mut request = [0_u8; 2048];
            if socket.read(&mut request)? == 0 {
                return Err(std::io::ErrorKind::UnexpectedEof.into());
            }
            socket.write_all(response.as_bytes())?;
            Ok(())
        });
        Ok((url, task))
    }
    #[tokio::test]
    async fn retry_cooldown_prevents_another_upstream_call(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let(url,task)=server("HTTP/1.1 429 Too Many Requests\r\nRetry-After: 7\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".into())?;
        let client = NativeMarketProvider::new(None)?;
        assert_eq!(
            client
                .get(&url, &[])
                .await
                .err()
                .and_then(|error| error.retry_after()),
            Some(7)
        );
        task.join().map_err(|_| "server failed")??;
        assert!(matches!(
            client.get(&url, &[]).await,
            Err(ProviderError::Upstream {
                retry_after: Some(_),
                ..
            })
        ));
        Ok(())
    }
    #[tokio::test]
    async fn redirects_and_oversized_bodies_are_rejected() -> Result<(), Box<dyn std::error::Error>>
    {
        let client = NativeMarketProvider::new(None)?;
        let(url,task)=server("HTTP/1.1 302 Found\r\nLocation: https://example.com/\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".into())?;
        assert!(matches!(
            client.get(&url, &[]).await,
            Err(ProviderError::Http {
                status: StatusCode::FOUND,
                ..
            })
        ));
        task.join().map_err(|_| "server failed")??;
        let (url, task) = server(format!(
            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
            MAX_BODY + 1
        ))?;
        assert!(matches!(
            client.get(&url, &[]).await,
            Err(ProviderError::Oversized)
        ));
        task.join().map_err(|_| "server failed")??;
        Ok(())
    }
    #[tokio::test]
    async fn public_cookie_response_holds_a_slot_until_consumed(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let client = NativeMarketProvider::new(None)?;
        let(url,task)=server("HTTP/1.1 404 Not Found\r\nSet-Cookie: A3=public-test; Path=/; Secure\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".into())?;
        let (response, slot) = client.request_status(client.client.get(url), true).await?;
        assert_eq!(response.status(), StatusCode::NOT_FOUND);
        assert_eq!(client.slots.available_permits(), 3);
        client.body(response).await?;
        drop(slot);
        assert_eq!(client.slots.available_permits(), 4);
        task.join().map_err(|_| "server failed")??;
        Ok(())
    }
}
