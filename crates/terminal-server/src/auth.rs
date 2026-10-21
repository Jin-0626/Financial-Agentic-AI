//! OIDC access-token verification; token headers never select remote destinations.
use axum::{
    http::{header, HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use jsonwebtoken::{decode, decode_header, jwk::JwkSet, Algorithm, DecodingKey, Validation};
use serde::Serialize;
use serde_json::{json, Value};
use std::{
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::sync::Mutex;

#[derive(Debug, thiserror::Error)]
pub enum AuthError {
    #[error("OIDC configuration is incomplete or invalid")]
    Configuration,
    #[error("Bearer access token required")]
    Missing,
    #[error("Invalid or expired access token")]
    Invalid,
    #[error("Identity provider unavailable")]
    Unavailable,
    #[error("Identity claim cannot be mapped to a workspace")]
    Identity,
}
impl IntoResponse for AuthError {
    fn into_response(self) -> Response {
        let status = match self {
            Self::Configuration | Self::Unavailable => StatusCode::SERVICE_UNAVAILABLE,
            Self::Missing | Self::Invalid => StatusCode::UNAUTHORIZED,
            Self::Identity => StatusCode::FORBIDDEN,
        };
        let mut response = (status, Json(json!({"detail":self.to_string()}))).into_response();
        if status == StatusCode::UNAUTHORIZED {
            response.headers_mut().insert(
                header::WWW_AUTHENTICATE,
                header::HeaderValue::from_static("Bearer"),
            );
        }
        response
    }
}
#[derive(Clone, Default)]
pub struct AuthConfig {
    pub production: bool,
    pub issuer: String,
    pub audience: String,
    pub jwks_url: String,
    pub client_id: String,
    pub scope: String,
    pub org_claim: String,
}
impl AuthConfig {
    pub fn from_environment() -> Result<Self, AuthError> {
        let get = |key| std::env::var(key).unwrap_or_default();
        let config = Self {
            production: get("APP_ENV").eq_ignore_ascii_case("production"),
            issuer: get("OIDC_ISSUER"),
            audience: get("OIDC_AUDIENCE"),
            jwks_url: get("OIDC_JWKS_URL"),
            client_id: get("OIDC_CLIENT_ID"),
            scope: std::env::var("OIDC_SCOPE").unwrap_or_else(|_| "openid profile".into()),
            org_claim: std::env::var("OIDC_ORG_CLAIM").unwrap_or_else(|_| "org_id".into()),
        };
        config.validate()?;
        Ok(config)
    }
    pub fn configured(&self) -> bool {
        [
            &self.issuer,
            &self.audience,
            &self.jwks_url,
            &self.client_id,
        ]
        .iter()
        .all(|v| !v.is_empty())
    }
    pub fn required(&self) -> bool {
        self.production || self.configured()
    }
    pub fn validate(&self) -> Result<(), AuthError> {
        let any = [
            &self.issuer,
            &self.audience,
            &self.jwks_url,
            &self.client_id,
        ]
        .iter()
        .any(|v| !v.is_empty());
        if (any || self.production) && !self.configured() {
            return Err(AuthError::Configuration);
        }
        if self.configured() {
            for value in [&self.issuer, &self.jwks_url] {
                let url = reqwest::Url::parse(value).map_err(|_| AuthError::Configuration)?;
                if url.scheme() != "https"
                    || url.host_str().is_none()
                    || !url.username().is_empty()
                    || url.password().is_some()
                    || url.fragment().is_some()
                {
                    return Err(AuthError::Configuration);
                }
            }
            if self.org_claim.is_empty() {
                return Err(AuthError::Configuration);
            }
        }
        Ok(())
    }
    pub fn public(&self) -> Value {
        json!({"required":self.required(),"configured":self.configured(),"authority":self.issuer,
            "client_id":self.client_id,"scope":self.scope,"audience":self.audience})
    }
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Identity {
    pub org: String,
    pub user: String,
}
pub fn claim_id(value: &str) -> Result<String, AuthError> {
    if value.is_empty()
        || value.len() > 128
        || value.contains("__")
        || !value
            .bytes()
            .all(|v| v.is_ascii_alphanumeric() || b"_.@|:-".contains(&v))
    {
        return Err(AuthError::Identity);
    }
    Ok(value.into())
}
#[derive(Default)]
struct Cache {
    keys: Option<JwkSet>,
    fetched: Option<Instant>,
    attempted: Option<Instant>,
}
#[derive(Clone)]
pub struct Authenticator {
    config: AuthConfig,
    client: reqwest::Client,
    cache: Arc<Mutex<Cache>>,
}
impl Authenticator {
    pub fn new(config: AuthConfig) -> Result<Self, AuthError> {
        config.validate()?;
        let client = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(3))
            .timeout(Duration::from_secs(5))
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(|_| AuthError::Configuration)?;
        Ok(Self {
            config,
            client,
            cache: Arc::new(Mutex::new(Cache::default())),
        })
    }
    pub fn config(&self) -> &AuthConfig {
        &self.config
    }
    async fn key(&self, kid: &str) -> Result<DecodingKey, AuthError> {
        let mut cache = self.cache.lock().await;
        let fresh = cache
            .fetched
            .is_some_and(|v| v.elapsed() < Duration::from_secs(300));
        if fresh {
            if let Some(key) = cache.keys.as_ref().and_then(|v| v.find(kid)) {
                return DecodingKey::from_jwk(key).map_err(|_| AuthError::Invalid);
            }
        }
        if cache
            .attempted
            .is_some_and(|v| v.elapsed() < Duration::from_secs(5))
        {
            return Err(if fresh {
                AuthError::Invalid
            } else {
                AuthError::Unavailable
            });
        }
        cache.attempted = Some(Instant::now());
        let mut response = self
            .client
            .get(&self.config.jwks_url)
            .send()
            .await
            .map_err(|_| AuthError::Unavailable)?;
        if !response.status().is_success() || response.content_length().is_some_and(|v| v > 262144)
        {
            return Err(AuthError::Unavailable);
        }
        let mut body = Vec::new();
        while let Some(chunk) = response.chunk().await.map_err(|_| AuthError::Unavailable)? {
            if body.len().saturating_add(chunk.len()) > 262144 {
                return Err(AuthError::Unavailable);
            }
            body.extend_from_slice(&chunk);
        }
        let keys: JwkSet = serde_json::from_slice(&body).map_err(|_| AuthError::Unavailable)?;
        cache.keys = Some(keys);
        cache.fetched = Some(Instant::now());
        let key = cache
            .keys
            .as_ref()
            .and_then(|v| v.find(kid))
            .ok_or(AuthError::Invalid)?;
        DecodingKey::from_jwk(key).map_err(|_| AuthError::Invalid)
    }
    pub async fn authenticate(&self, headers: &HeaderMap) -> Result<Option<Identity>, AuthError> {
        if !self.config.required() {
            return Ok(None);
        }
        let authorization = headers
            .get(header::AUTHORIZATION)
            .and_then(|v| v.to_str().ok())
            .ok_or(AuthError::Missing)?;
        let (scheme, token) = authorization.split_once(' ').ok_or(AuthError::Missing)?;
        if !scheme.eq_ignore_ascii_case("bearer") || token.is_empty() || token.len() > 16384 {
            return Err(AuthError::Missing);
        }
        let header = decode_header(token).map_err(|_| AuthError::Invalid)?;
        if !matches!(header.alg, Algorithm::RS256 | Algorithm::ES256) {
            return Err(AuthError::Invalid);
        }
        let kid = header
            .kid
            .as_deref()
            .filter(|v| !v.is_empty() && v.len() <= 256)
            .ok_or(AuthError::Invalid)?;
        let key = self.key(kid).await?;
        let mut validation = Validation::new(header.alg);
        validation.set_audience(&[&self.config.audience]);
        validation.set_issuer(&[&self.config.issuer]);
        validation.set_required_spec_claims(&["exp", "iat", "iss", "aud", "sub"]);
        validation.leeway = 15;
        validation.validate_nbf = true;
        let claims = decode::<Value>(token, &key, &validation)
            .map_err(|_| AuthError::Invalid)?
            .claims;
        let issued = claims["iat"].as_i64().ok_or(AuthError::Invalid)?;
        if issued > chrono::Utc::now().timestamp() + 15 {
            return Err(AuthError::Invalid);
        }
        Ok(Some(Identity {
            org: claim_id(
                claims
                    .get(&self.config.org_claim)
                    .and_then(Value::as_str)
                    .ok_or(AuthError::Identity)?,
            )?,
            user: claim_id(claims["sub"].as_str().ok_or(AuthError::Identity)?)?,
        }))
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn production_and_partial_configuration_fail_closed() {
        assert!(AuthConfig {
            production: true,
            ..Default::default()
        }
        .validate()
        .is_err());
        assert!(AuthConfig {
            issuer: "https://issuer.example".into(),
            ..Default::default()
        }
        .validate()
        .is_err());
        assert!(claim_id("../other").is_err());
        assert!(claim_id("org__user").is_err());
        assert!(claim_id("user@example.com").is_ok());
    }
    #[tokio::test]
    async fn local_mode_does_not_treat_bearer_as_identity() -> Result<(), AuthError> {
        let auth = Authenticator::new(AuthConfig::default())?;
        assert_eq!(auth.authenticate(&HeaderMap::new()).await?, None);
        Ok(())
    }
}

#[cfg(test)]
mod verification_tests {
    use super::*;
    use jsonwebtoken::{encode, EncodingKey, Header};
    fn claims() -> Value {
        let now = chrono::Utc::now().timestamp();
        json!({"sub":"alice","org_id":"org-a","iss":"https://issuer.example","aud":"terminal","iat":now,"exp":now+600})
    }
    async fn authenticator() -> Result<Authenticator, Box<dyn std::error::Error + Send + Sync>> {
        let auth = Authenticator::new(AuthConfig {
            production: true,
            issuer: "https://issuer.example".into(),
            audience: "terminal".into(),
            jwks_url: "https://issuer.example/jwks".into(),
            client_id: "desktop".into(),
            scope: "openid profile".into(),
            org_claim: "org_id".into(),
        })?;
        {
            let mut cache = auth.cache.lock().await;
            cache.keys = Some(serde_json::from_str(include_str!(
                "../tests/fixtures/test-jwks.json"
            ))?);
            cache.fetched = Some(Instant::now());
            cache.attempted = Some(Instant::now());
        }
        Ok(auth)
    }
    fn headers(claims: Value) -> Result<HeaderMap, Box<dyn std::error::Error + Send + Sync>> {
        let key =
            EncodingKey::from_rsa_der(include_bytes!("../tests/fixtures/test-signing-key.der"));
        let mut header = Header::new(Algorithm::RS256);
        header.kid = Some("fixture".into());
        let token = encode(&header, &claims, &key)?;
        let mut headers = HeaderMap::new();
        headers.insert(header::AUTHORIZATION, format!("Bearer {token}").parse()?);
        Ok(headers)
    }
    #[tokio::test]
    async fn signatures_claims_expiry_and_identity_are_verified(
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let auth = authenticator().await?;
        assert_eq!(
            auth.authenticate(&headers(claims())?).await?,
            Some(Identity {
                org: "org-a".into(),
                user: "alice".into()
            })
        );
        for (name, value) in [
            ("aud", json!("another-service")),
            ("iss", json!("https://other.example")),
            ("exp", json!(chrono::Utc::now().timestamp() - 100)),
            ("iat", json!(chrono::Utc::now().timestamp() + 600)),
            ("nbf", json!(chrono::Utc::now().timestamp() + 600)),
            ("org_id", Value::Null),
            ("sub", json!("other__tenant")),
        ] {
            let mut bad = claims();
            bad[name] = value;
            assert!(auth.authenticate(&headers(bad)?).await.is_err(), "{name}");
        }
        let mut missing = claims();
        missing.as_object_mut().ok_or("claims")?.remove("exp");
        assert!(auth.authenticate(&headers(missing)?).await.is_err());
        let valid = headers(claims())?;
        let token = valid
            .get(header::AUTHORIZATION)
            .ok_or("authorization")?
            .to_str()?;
        let index = token.rfind('.').ok_or("signature")? + 1;
        let mut forged = token.as_bytes().to_vec();
        forged[index] = if forged[index] == b'A' { b'B' } else { b'A' };
        let mut bad = HeaderMap::new();
        bad.insert(header::AUTHORIZATION, String::from_utf8(forged)?.parse()?);
        assert!(matches!(
            auth.authenticate(&bad).await,
            Err(AuthError::Invalid)
        ));
        assert!(matches!(
            auth.authenticate(&HeaderMap::new()).await,
            Err(AuthError::Missing)
        ));
        Ok(())
    }
}
