import os
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.bootstrap.market_application import app


class NativeGatewayTests(unittest.TestCase):
    def test_gateway_serves_existing_market_contract_without_other_services(self):
        environment = {
            "APP_ENV": "local",
            "OIDC_ISSUER": "",
            "OIDC_AUDIENCE": "",
            "OIDC_JWKS_URL": "",
            "OIDC_CLIENT_ID": "",
        }
        payload = {"status": "ok", "data": {"price": "12.34"}, "source": "fixture"}
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(
                os.environ,
                {
                    **environment,
                    "PORTFOLIO_SQLITE_PATH": str(Path(directory) / "portfolio.sqlite3"),
                },
            ),
            patch("app.market.routes.data_quotes.quote", return_value=payload) as provider,
        ):
            with TestClient(app) as client:
                self.assertEqual(client.get("/api/health").json()["service"], "market-data")
                self.assertEqual(client.get("/api/auth/me").json(), {"local": True})
                response = client.get("/api/market/quote", params={"instrument": "yahoo:5326.KL"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), payload)
                provider.assert_called_once_with("yahoo:5326.KL")
                self.assertEqual(client.get("/api/portfolio").json(), {"portfolio": None})
                created = client.post(
                    "/api/portfolio/create",
                    json={"name": "Local", "currency": "MYR", "positions": []},
                )
                self.assertEqual(created.status_code, 200)
                pid = created.json()["id"]
                self.assertEqual(
                    client.get("/api/portfolio/list").json()["portfolios"][0]["id"], pid
                )
                capabilities = client.get("/api/market/capabilities").json()
                self.assertEqual(capabilities["service"], "market-data")
                self.assertTrue(capabilities["features"]["portfolio"])
                self.assertFalse(capabilities["features"]["research"])
                self.assertNotIn("portfolio", capabilities["widgets"])
            with TestClient(app) as reopened:
                self.assertEqual(
                    reopened.get("/api/portfolio/list").json()["portfolios"][0]["id"], pid
                )
