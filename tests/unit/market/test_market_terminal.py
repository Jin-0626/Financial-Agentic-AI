import asyncio
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.market.news as data_news
import app.market.quotes as data_quotes
import app.market.common as data_common
import app.market.transport as data_transport
import app.market.calendar as data_calendar
import app.market.options as data_options
import app.market.screening as data_screening
import app.market.history as data_history
from app.market.routes import router
from app.market.service import MarketService
from app.research.schemas import ChatRequest


class MarketAdapterTests(unittest.TestCase):
    def test_instrument_identity_and_nonfinite_values(self):
        self.assertNotEqual(
            data_common.instrument("coingecko:bitcoin")["id"],
            data_common.instrument("yahoo:BTC-USD")["id"],
        )
        self.assertEqual(data_common.instrument("yahoo:5326.KL")["symbol"], "5326.KL")
        for value in (True, float("nan"), float("inf"), None):
            self.assertIsNone(data_common.number(value))
        with self.assertRaises(ValueError):
            data_common.instrument("coingecko:../../secret")
        with self.assertRaises(ValueError):
            data_transport.fetch("http://127.0.0.1/secret")

    def test_crypto_quote_cannot_substitute_another_asset(self):
        with (
            patch.object(data_transport, "cg",
                return_value=[{"id": "ethereum", "symbol": "eth", "current_price": 20}],
            ),
            self.assertRaises(ValueError),
        ):
            data_quotes.quote("coingecko:bitcoin")

    def test_daily_policy_outcome_requires_explicit_effective_date(self):
        rows = [{"date": "2026-10-01", "value": "2.5"}]
        event = {
            "title": "ECB Deposit Facility Rate",
            "country": "EUR",
            "reference_period": "2026-10-01",
        }
        self.assertEqual(data_calendar.verified_calendar_actual(event, rows)["value"], "2.5%")
        self.assertIsNone(
            data_calendar.verified_calendar_actual(
                {**event, "reference_period": "2026-10"}, rows
            )
        )

    def test_quote_preserves_missing_and_currency(self):
        with patch.object(data_common, "ticker_info",
            return_value=(
                MagicMock(),
                {
                    "regularMarketPrice": 3.2,
                    "currency": "MYR",
                    "regularMarketTime": 1700000000,
                },
            ),
        ):
            result = data_quotes.quote("yahoo:5326.KL")
        self.assertEqual(result["data"]["price"], "3.2")
        self.assertIsNone(result["data"]["bid"])
        self.assertEqual(result["data"]["currency"], "MYR")
        self.assertIsNotNone(result["observed_at"])

    def test_news_deduplicates_headline_and_tracking_urls(self):
        rows = [
            {
                "title": "Company report",
                "url": "https://example.com/story?utm_source=a",
                "provider": "Publisher",
                "pub_date": "2026-10-01",
            },
            {
                "title": "Company report",
                "url": "https://elsewhere.com/story",
                "provider": "Other",
            },
        ]
        with (
            patch("orchestrator.tools.providers.news._public_market_news", return_value=rows),
            patch.dict("os.environ", {"FMP_API_KEY": ""}),
        ):
            result = data_news.news()
        self.assertEqual(len(result["data"]), 1)
        self.assertEqual(result["data"][0]["url"], "https://example.com/story")
        self.assertEqual(result["data"][0]["published_at"], "2026-10-01")

    def test_bursa_options_are_explicitly_unsupported(self):
        self.assertEqual(data_options.options("yahoo:5326.KL")["status"], "unavailable")

    def test_calendar_does_not_invent_an_actual(self):
        with patch.object(data_transport, "fetch",
            return_value=[
                {
                    "title": "CPI",
                    "country": "USD",
                    "forecast": "2%",
                    "previous": "1%",
                    "date": "2026-10-12",
                }
            ],
        ):
            result = data_calendar.economic_calendar()
        self.assertIsNone(result["data"][0]["actual"])
        self.assertEqual(result["data"][0]["forecast"], "2%")

    def test_calendar_actual_requires_country_and_exact_reference_period(self):
        observations = [
            {"date": "2026-09-01", "value": "110"},
            {"date": "2025-09-01", "value": "100"},
            {"date": "2026-08-01", "value": "109"},
        ]
        row = {"title": "CPI y/y", "country": "USD", "reference_period": "2026-09"}
        self.assertEqual(
            data_calendar.verified_calendar_actual(row, observations)["value"], "10.0%"
        )
        self.assertIsNone(
            data_calendar.verified_calendar_actual({**row, "country": "MYR"}, observations)
        )
        self.assertIsNone(
            data_calendar.verified_calendar_actual(
                {**row, "reference_period": "2026-10"}, observations
            )
        )
        self.assertIsNone(
            data_calendar.verified_calendar_actual(
                {"title": "CPI y/y", "country": "USD"}, observations
            )
        )

    def test_earnings_move_uses_next_actual_bar_and_unknown_time_stays_null(self):
        import pandas as pd

        when = pd.DatetimeIndex(
            ["2026-10-01 16:30", "2026-09-01 00:00"], tz="America/New_York"
        )
        earnings = pd.DataFrame(
            {"EPS Estimate": [1.0, 1.0], "Reported EPS": [1.1, 1.2]}, index=when
        )
        history = pd.DataFrame(
            {"Close": [100, 110]},
            index=pd.DatetimeIndex(["2026-10-01", "2026-10-02"], tz="America/New_York"),
        )
        ticker = MagicMock()
        ticker.get_earnings_dates.return_value = earnings
        ticker.history.return_value = history
        with patch.object(data_transport.yf, "Ticker", return_value=ticker):
            result = data_calendar.earnings("yahoo:AAPL")
        self.assertAlmostEqual(result["data"][0]["next_session_move_percent"], 10)
        self.assertIsNone(result["data"][1]["next_session_move_percent"])

    def test_screener_preserves_provider_total_and_page(self):
        with patch.object(
            data_transport.yf,
            "screen",
            return_value={
                "total": 500,
                "quotes": [
                    {
                        "symbol": "AAPL",
                        "currency": "USD",
                        "marketCap": 123,
                        "regularMarketChangePercent": 2,
                    }
                ],
            },
        ):
            result = data_screening.screener(
                "US", None, None, None, None, "market_cap", True, 100, 100
            )
        self.assertEqual(result["total"], 500)
        self.assertEqual(result["offset"], 100)
        self.assertIn("not verified", result["coverage"])

    def test_ohlcv_orders_deduplicates_and_drops_invalid_bars(self):
        import pandas as pd

        index = pd.DatetimeIndex(
            ["2026-10-02", "2026-10-01", "2026-10-02", "2026-10-03"],
            tz="Asia/Kuala_Lumpur",
        )
        frame = pd.DataFrame(
            {
                "Open": [10, 10, 10, 10],
                "High": [12, 12, 12, 8],
                "Low": [9, 9, 9, 9],
                "Close": [11, 11, 11, 11],
                "Volume": [100, 100, 100, 100],
            },
            index=index,
        )
        ticker = MagicMock()
        ticker.history.return_value = frame
        with patch.object(data_common, "ticker_info", return_value=(ticker, {"currency": "MYR"})
        ):
            value = data_history.ohlcv("yahoo:5326.KL", "1M")
        self.assertEqual(len(value["data"]), 2)
        self.assertLess(value["data"][0]["time"], value["data"][1]["time"])
        self.assertEqual(value["currency"], "MYR")
        self.assertTrue(value["limitations"])
        ticker.history.assert_called_once_with(
            period="1mo", interval="1d", auto_adjust=False, actions=False, timeout=10
        )

    def test_option_expiry_mismatch_is_not_silently_replaced(self):
        ticker = MagicMock()
        ticker.options = ["2026-10-30"]
        with (
            patch.object(data_transport.yf, "Ticker", return_value=ticker),
            self.assertRaises(ValueError),
        ):
            data_options.options("yahoo:AAPL", "2026-11-01")
        ticker.option_chain.assert_not_called()

    def test_context_is_bounded_and_does_not_accept_other_fields(self):
        self.assertEqual(
            ChatRequest(
                terminal_context={"instrument": {"id": "yahoo:5326.KL"}}
            ).terminal_context["instrument"]["id"],
            "yahoo:5326.KL",
        )
        for value in (
            {"secret": "x"},
            {"quote": {"price": float("nan")}},
            {"quote": {"text": "x" * 9000}},
        ):
            with self.assertRaises(ValueError):
                ChatRequest(terminal_context=value)


class MarketServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = MarketService()

    async def asyncTearDown(self):
        self.service.close()

    async def test_shared_single_flight_and_defensive_copy(self):
        count = 0

        def load():
            nonlocal count
            count += 1
            time.sleep(0.03)
            return data_common.result({"price": "10"}, "Fixture", "Synthetic")

        results = await asyncio.gather(
            *(self.service.get(("quote", "A"), "fixture", 15, load) for _ in range(8))
        )
        self.assertEqual(count, 1)
        results[0]["data"]["price"] = "changed"
        self.assertEqual(results[1]["data"]["price"], "10")

    async def test_provider_bound_and_cancelled_subscriber(self):
        lock = threading.Lock()
        running = peak = 0

        def load():
            nonlocal running, peak
            with lock:
                running += 1
                peak = max(peak, running)
            time.sleep(0.04)
            with lock:
                running -= 1
            return data_common.result([], "Fixture", "Synthetic")

        tasks = [
            asyncio.create_task(self.service.get(("q", i), "fixture", 0, load))
            for i in range(8)
        ]
        tasks[0].cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.assertLessEqual(peak, 4)
        self.assertEqual(running, 0)

    async def test_cancelled_subscriber_does_not_cancel_shared_load(self):
        def load():
            time.sleep(0.04)
            return data_common.result({"price": "10"}, "Fixture", "Synthetic")

        first = asyncio.create_task(self.service.get(("shared",), "fixture", 15, load))
        second = asyncio.create_task(self.service.get(("shared",), "fixture", 15, load))
        await asyncio.sleep(0.01)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertEqual((await second)["data"]["price"], "10")

    async def test_oversized_and_nonfinite_output_fail_closed(self):
        for value in (
            data_common.result({"blob": "x" * (2 * 1024 * 1024)}, "Fixture", "Synthetic"),
            data_common.result({"value": float("inf")}, "Fixture", "Synthetic"),
        ):
            result = await self.service.get(
                ("bound", id(value)), "fixture", 0, lambda value=value: value
            )
            self.assertEqual(result["status"], "unavailable")
            self.assertNotIn("blob", str(result))

    async def test_rate_limit_cooldown_and_stale_snapshot(self):
        await self.service.get(
            ("q",),
            "fixture",
            0,
            lambda: data_common.result({"price": "10"}, "Fixture", "Synthetic"),
        )

        def fail():
            raise data_transport.Throttled(20)

        stale = await self.service.get(("q",), "fixture", 0, fail)
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(stale["data"]["price"], "10")
        other = await self.service.get(("different",), "fixture", 0, lambda: None)
        self.assertEqual(other["status"], "unavailable")
        self.assertGreater(other["retry_after"], 0)


class MarketRouteTests(unittest.TestCase):
    def test_validation_auth_and_public_response(self):
        app = FastAPI()
        app.include_router(router, prefix="/api")
        with TestClient(app) as client:
            self.assertEqual(
                client.get(
                    "/api/market/ohlcv?instrument=yahoo:AAPL&range=invalid"
                ).status_code,
                422,
            )
            self.assertEqual(
                client.get("/api/market/quote?instrument=http://private").status_code,
                422,
            )
            with patch(
                "app.market.routes.cached",
                return_value=data_common.result([], "Fixture", "Synthetic"),
            ):
                self.assertEqual(
                    client.get("/api/market/quote?instrument=yahoo:AAPL").status_code,
                    200,
                )
            with patch.dict("os.environ", {"APP_ENV": "production"}):
                self.assertEqual(
                    client.get("/api/market/capabilities").status_code, 503
                )


class TerminalContextTests(unittest.TestCase):
    def test_model_receives_unverified_context_without_mutating_history(self):
        from types import SimpleNamespace

        from langchain.agents.middleware.types import ModelRequest
        from langchain_core.messages import HumanMessage

        from contracts.context import MemoryContext
        from orchestrator.budgets import ExecutionLimits
        from tests.support.agent_fixtures import ToolModel

        context = {
            "instrument": {"id": "yahoo:5326.KL"},
            "quote": {"price": "3.2", "currency": "MYR"},
        }
        original = HumanMessage(content="Review this company")
        request = ModelRequest(
            model=ToolModel(responses=[]),
            messages=[original],
            tools=[],
            runtime=SimpleNamespace(context=MemoryContext(terminal_context=context)),
        )
        bounded = ExecutionLimits().bounded_request(request, None)
        self.assertEqual(request.messages, [original])
        self.assertIn("unverified data", bounded.messages[-1].content)
        self.assertIn("yahoo:5326.KL", bounded.messages[-1].content)
        self.assertNotIn("portfolio", bounded.messages[-1].content)
