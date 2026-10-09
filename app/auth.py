"""Verified OIDC identity. Caller-supplied scopes are local-development conveniences."""

import asyncio
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlparse

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request


@dataclass(frozen=True)
class Identity:
    org: str
    user: str


def production():
    return os.getenv("APP_ENV", "local").lower() == "production"


def configured():
    return all(
        os.getenv(name)
        for name in ("OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL", "OIDC_CLIENT_ID")
    )


def validate_config():
    if not configured():
        raise HTTPException(503, "OIDC identity is not configured")
    for name in ("OIDC_ISSUER", "OIDC_JWKS_URL"):
        parsed = urlparse(os.environ[name])
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise HTTPException(503, "OIDC endpoints must use HTTPS")


@lru_cache(maxsize=4)
def jwks_client(url):
    return jwt.PyJWKClient(url, cache_jwk_set=True, lifespan=300, timeout=5)


def verify(token):
    validate_config()
    key = jwks_client(os.environ["OIDC_JWKS_URL"]).get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        key.key,
        algorithms=["RS256", "ES256"],
        audience=os.environ["OIDC_AUDIENCE"],
        issuer=os.environ["OIDC_ISSUER"],
        options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        leeway=15,
    )


def claim_id(value):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9_.@|:-]{1,128}", value)
        or "__" in value
    ):
        raise HTTPException(403, "Identity claim cannot be mapped to a workspace")
    return value


async def authenticated_identity(request: Request):
    if not production() and not configured():
        return None
    validate_config()
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or len(token) > 16384:
        raise HTTPException(
            401, "Bearer access token required", headers={"WWW-Authenticate": "Bearer"}
        )
    try:
        claims = await asyncio.wait_for(asyncio.to_thread(verify, token), timeout=8)
    except (jwt.PyJWKClientConnectionError, asyncio.TimeoutError):
        raise HTTPException(503, "Identity provider unavailable") from None
    except jwt.PyJWTError:
        raise HTTPException(
            401,
            "Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    identity = Identity(
        claim_id(claims.get(os.getenv("OIDC_ORG_CLAIM", "org_id"))),
        claim_id(claims.get("sub")),
    )
    return identity


async def organization(request: Request, identity=Depends(authenticated_identity)):
    return (
        identity.org
        if identity
        else claim_id(request.query_params.get("org_id", "default-org"))
    )


async def user(request: Request, identity=Depends(authenticated_identity)):
    return (
        identity.user
        if identity
        else claim_id(request.query_params.get("user_id", "local-user"))
    )


router = APIRouter(prefix="/auth", tags=["identity"])


@router.get("/config")
async def configuration():
    required = production() or configured()
    if configured():
        validate_config()
    return {
        "required": required,
        "configured": configured(),
        "authority": os.getenv("OIDC_ISSUER", ""),
        "client_id": os.getenv("OIDC_CLIENT_ID", ""),
        "scope": os.getenv("OIDC_SCOPE", "openid profile"),
        "audience": os.getenv("OIDC_AUDIENCE", ""),
    }


@router.get("/me")
async def me(identity=Depends(authenticated_identity)):
    if identity is None:
        return {"local": True}
    return {"local": False, "org": identity.org, "user": identity.user}
