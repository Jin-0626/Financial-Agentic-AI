"""Loopback-only production-build fixture: real SQLite/Rust, deterministic model/providers.
Never import this fixture from application code or deploy it as a production server.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import time
import tempfile
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from langchain_core.messages import AIMessage
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
import app.bootstrap.application as app
import app.infrastructure.auth as auth
import app.infrastructure.providers as providers
from orchestrator import agent


class FixtureModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
issuer = "https://fixture.invalid"
os.environ.update(
    APP_ENV="production",
    RESEARCH_ENABLED="true",
    OIDC_ISSUER=issuer,
    OIDC_JWKS_URL=issuer + "/jwks",
    OIDC_CLIENT_ID="terminal-e2e",
    OIDC_AUDIENCE="terminal",
    OIDC_ORG_CLAIM="org_id",
    PORTFOLIO_ENABLED="true",
)


def session(org="production-test", user="alice"):
    now = int(time.time())
    claims = {
        "iss": issuer,
        "aud": "terminal",
        "iat": now,
        "exp": now + 3600,
        "sub": user,
        "org_id": org,
    }
    token = jwt.encode(claims, key, algorithm="RS256")
    return {
        "access_token": token,
        "id_token": token,
        "token_type": "Bearer",
        "scope": "openid profile",
        "profile": {"sub": user, "org_id": org},
        "expires_at": now + 3600,
    }


def quote(symbol):
    symbol = providers.symbol(symbol)
    if symbol not in {"AAPL", "SPY"}:
        raise ValueError("Quote unavailable in deterministic fixture")
    return {
        "symbol": symbol,
        "price": "120" if symbol == "AAPL" else "400",
        "currency": "USD",
        "name": "Apple" if symbol == "AAPL" else "SPY",
        "sector": "Technology",
        "exchange": "NASDAQ",
        "market_state": "CLOSED",
        "as_of": 1760000000,
        "change_percent": 0,
        "source": "Deterministic Yahoo fixture",
        "fetched_at": "2026-10-09T00:00:00Z",
    }


def history(symbols, currencies, period):
    return [
        {
            "symbol": s,
            "currency": currencies[s],
            "prices": [
                {"date": "2025-01-01", "close": "100"},
                {"date": "2025-01-02", "close": "120"},
            ],
        }
        for s in symbols
    ]


original_lifespan = app.lifespan


@asynccontextmanager
async def lifespan(application):
    temporary = tempfile.TemporaryDirectory(prefix="terminal_e2e_")
    directory = Path(temporary.name)
    model = FixtureModel(
        responses=[
            AIMessage(
                content="## Production fixture report\n\nReadable persisted research.",
                usage_metadata={
                    "input_tokens": 10,
                    "output_tokens": 10,
                    "total_tokens": 20,
                },
            )
        ]
    )
    try:
        with (
            patch.object(app, "research_path", return_value=directory / "research.sqlite3"),
            patch.dict(
                os.environ, {"PORTFOLIO_SQLITE_PATH": str(directory / "portfolios.sqlite3")}
            ),
            patch.object(
                auth,
                "jwks_client",
                return_value=SimpleNamespace(
                    get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())
                ),
            ),
            patch.object(providers, "quote", quote),
            patch.object(providers, "price_series", history),
            patch.object(
                providers,
                "search_symbols",
                lambda query, count=8: [{"symbol": "AAPL", "name": "Apple", "exchange": "NASDAQ"}],
            ),
            patch.object(agent, "_configured_model", lambda: model),
        ):
            async with original_lifespan(application):
                yield
    finally:
        temporary.cleanup()


app.app.router.lifespan_context = lifespan


@app.app.get("/fixture/session")
async def fixture_session(org: str = "production-test", user: str = "alice"):
    return session(org, user)


app.app.router.routes.insert(0, app.app.router.routes.pop())

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app.app, host="127.0.0.1", port=8019, log_level="warning")
