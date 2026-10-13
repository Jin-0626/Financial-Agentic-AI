import os, unittest
from unittest.mock import patch, Mock, AsyncMock
import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import providers, portfolio_service as service, portfolio_routes as routes
from app.portfolio_repository import MemoryPortfolioRepository


class ProviderTests(unittest.TestCase):
    def test_no_exchange_guessing_and_quote_conditions(self):
        with patch.object(providers.yf, "Ticker") as ticker:
            ticker.return_value.get_info.return_value = {
                "symbol": "AAPL.KL",
                "regularMarketPrice": 100,
                "currency": "MYR",
            }
            with self.assertRaisesRegex(ValueError, "exact symbol"):
                providers.quote("AAPL")
            ticker.assert_called_once_with("AAPL")
            ticker.return_value.get_info.return_value = {
                "symbol": "AAPL",
                "regularMarketPrice": 100,
                "regularMarketPreviousClose": 100,
                "currency": "USD",
                "marketState": "CLOSED",
                "regularMarketTime": 1000,
            }
            q = providers.quote("AAPL")
            self.assertEqual(q["price"], "100")
            self.assertEqual(q["change_percent"], 0)
            self.assertEqual(q["as_of"], 1000)

    def test_common_provider_history_preserves_missing_dates(self):
        frame = pd.DataFrame(
            {("AAPL", "Close"): [10, 11, 12], ("SPY", "Close"): [20, None, 21]},
            index=pd.date_range("2025-01-01", periods=3),
        )
        with patch.object(providers.yf, "download", return_value=frame) as download:
            series = providers.price_series(
                ["AAPL", "SPY"], {"AAPL": "USD", "SPY": "USD"}, "1mo"
            )
        self.assertEqual(len(series[1]["prices"]), 2)
        self.assertTrue(download.call_args.kwargs["auto_adjust"])

    def test_invalid_history_and_symbols(self):
        with patch.object(providers.yf, "download", return_value=pd.DataFrame()):
            with self.assertRaisesRegex(ValueError, "history"):
                providers.price_series(["AAPL"], {"AAPL": "USD"}, "1mo")
        with self.assertRaises(ValueError):
            providers.symbol("1155/KL")
        with self.assertRaises(ValueError):
            service.Trade(
                id="x",
                symbol="AAPL",
                side="BUY",
                quantity="NaN",
                price="1",
                date="2025-01-01",
            )


class PortfolioIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_portfolios_are_scoped_and_reloaded(self):
        repo = MemoryPortfolioRepository()
        doc = await service.create_portfolio(
            repo, "org", "user", service.Portfolio(name="Saved")
        )
        self.assertEqual(
            (await service.load_portfolio(repo, "org", "user", doc["id"]))["portfolio"][
                "name"
            ],
            "Saved",
        )
        self.assertIsNone(
            await service.load_portfolio(repo, "other", "user", doc["id"])
        )
        self.assertIsNone(await service.load_portfolio(repo, "org", "other", doc["id"]))

    async def test_missing_quote_cannot_produce_partial_total(self):
        p = service.Portfolio(
            positions=[{"symbol": "AAPL", "quantity": "2", "average_cost": "10"}]
        )
        with (
            patch.object(providers, "quote", side_effect=ValueError("Unavailable")),
            patch.object(service, "calculate", AsyncMock()) as native,
        ):
            result = await service.snapshot("org", p)
        self.assertIsNone(result["market_value"])
        self.assertEqual(len(result["errors"]), 1)
        native.assert_not_called()

    async def test_production_and_local_feature_gates(self):
        app = FastAPI()
        app.include_router(routes.router, prefix="/api")
        repo = MemoryPortfolioRepository()
        with (
            patch.object(routes, "_repository", repo),
            patch.dict(os.environ, {"APP_ENV": "local", "PORTFOLIO_ENABLED": "true"}),
            TestClient(app) as client,
        ):
            result = client.post(
                "/api/portfolio/create?org_id=o&user_id=u", json={"name": "Saved"}
            )
            self.assertEqual(result.status_code, 200)
            pid = result.json()["id"]
            self.assertEqual(
                client.get(
                    f"/api/portfolio?org_id=o&user_id=u&portfolio_id={pid}"
                ).json()["portfolio"]["name"],
                "Saved",
            )
            with patch.dict(os.environ, {"APP_ENV": "production"}):
                self.assertEqual(
                    client.get("/api/portfolio/list?org_id=o&user_id=u").status_code,
                    404,
                )
            with patch.dict(os.environ, {"PORTFOLIO_ENABLED": "false"}):
                self.assertEqual(
                    client.get("/api/portfolio/list?org_id=o&user_id=u").status_code,
                    404,
                )
