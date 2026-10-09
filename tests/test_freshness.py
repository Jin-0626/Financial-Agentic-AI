import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
import pandas as pd
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from langgraph.store.memory import InMemoryStore

from app import financial_tools as finance
from scripts.data_sources import yf_data
from orchestrator.freshness import ResearchFreshness
from orchestrator import agent
from app.context_type import MemoryContext
from agent_fixtures import ToolModel

CURRENT = datetime(2026, 10, 9, 1, 0, tzinfo=timezone.utc)


class FreshnessTests(unittest.TestCase):
    def test_news_discards_old_future_and_orders_recent_without_inventing_dates(self):
        articles = [
            {"title": "Old", "pub_date": "2024-01-01"},
            {"title": "New", "pub_date": "2026-10-08"},
            {"title": "Older recent", "pub_date": "2026-10-05"},
            {"title": "Undated"},
            {"title": "Future", "pub_date": "2027-01-01"},
        ]
        with patch.object(finance, "now_utc", return_value=CURRENT):
            current = finance._recent_news(articles, 7)
            historical = finance._recent_news(articles, None)
        self.assertEqual(
            [a["title"] for a in current], ["New", "Older recent", "Undated"]
        )
        self.assertEqual(current[-1]["publication_status"], "unknown")
        self.assertNotIn("pub_date", current[-1])
        self.assertIn("Old", [a["title"] for a in historical])

    def test_rfc_news_dates_are_recognized_and_old_items_filtered(self):
        with patch.object(finance, "now_utc", return_value=CURRENT):
            rows = finance._recent_news([
                {"title": "Current", "pub_date": "Wed, 07 Oct 2026 00:29:59 GMT"},
                {"title": "Old", "pub_date": "Mon, 07 Oct 2024 00:29:59 GMT"}], 7)
        self.assertEqual([row["title"] for row in rows], ["Current"])
        self.assertEqual(rows[0]["publication_status"], "dated")

    def test_statement_report_figures_preserve_base_units(self):
        frame = pd.DataFrame({pd.Timestamp("2026-04-30"): [878460000, 55000, 0.0001]},
                             index=["Total Revenue", "Net Income", "Diluted EPS"])
        ticker = SimpleNamespace(financials=frame, balance_sheet=frame, cashflow=frame)
        with patch("app.providers.ticker_info", return_value=(ticker, {"financialCurrency": "MYR"})), patch("app.providers.refresh_statement_cache"):
            result = yf_data.get_financials("5275.KL", frequency="annual")
        displays = [row["display"] for row in result["report_figures"]]
        self.assertIn("MYR 878,460,000.00", displays)
        self.assertIn("MYR 55,000.00", displays)
        self.assertFalse(any(row["field"] == "Diluted EPS" for row in result["report_figures"]))
        self.assertEqual(result["audit_status"], "Not verified from an audited filing")

    def test_batch_quotes_use_market_time_and_preserve_partial_failures(self):
        def provider(symbol):
            if symbol == "BAD":
                raise ValueError("Quote unavailable")
            return {"symbol": symbol, "price": "12.5", "as_of": 1728000000,
                    "currency": "MYR", "fetched_at": CURRENT.isoformat()}

        with patch("app.providers.quote", side_effect=provider) as fetch:
            rows = yf_data.get_batch_quotes(["1155.KL", "BAD", "1155.KL"])
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(rows[0]["timestamp"], 1728000000)
        self.assertEqual(rows[0]["currency"], "MYR")
        self.assertEqual(rows[0]["price"], 12.5)
        self.assertEqual(rows[1]["symbol"], "BAD")
        self.assertIn("error", rows[1])
        with patch("app.providers.quote") as fetch:
            self.assertIn("error", yf_data.get_batch_quotes(["A"] * 51)[0])
            fetch.assert_not_called()
        self.assertEqual(yf_data.get_batch_quotes([]), [])

    def test_new_requests_clear_persisted_plan_and_resume_preserves_it(self):
        import asyncio
        from unittest.mock import AsyncMock
        from app import routes
        from langgraph.types import Command

        runner = SimpleNamespace(ainvoke=AsyncMock(return_value={"messages": []}))
        with patch.object(routes, "_agent", runner), patch.object(
            routes, "_ensure_user_habits", new=AsyncMock()
        ):
            asyncio.run(routes._chat("Analyze Maybank", "thread", "user", "org"))
            request = runner.ainvoke.call_args.args[0]
            self.assertEqual(request["todos"], [])
            self.assertEqual(request["messages"][0]["content"], "Analyze Maybank")
            asyncio.run(routes._chat("", "thread", "user", "org", resume={"decisions": []}))
            self.assertIsInstance(runner.ainvoke.call_args.args[0], Command)

    def test_stream_starts_with_empty_plan(self):
        import asyncio
        from unittest.mock import AsyncMock
        from app import routes

        captured = []
        async def stream(request, **kwargs):
            captured.append(request)
            yield (), "values", {"messages": [AIMessage(content="Current analysis")], "todos": []}

        async def consume():
            return [event async for event in routes._stream_sse("Analyze Maybank", "thread", "user", "org")]

        with patch.object(routes, "_agent", SimpleNamespace(astream=stream)), patch.object(
            routes, "_ensure_user_habits", new=AsyncMock()
        ):
            events = asyncio.run(consume())
        self.assertTrue(events)
        self.assertEqual(captured[0]["todos"], [])
        self.assertEqual(captured[0]["messages"][0]["content"], "Analyze Maybank")

    def test_search_requests_provider_date_bounds(self):
        response = Mock()
        response.json.return_value = {"results": []}
        with (
            patch.object(finance, "now_utc", return_value=CURRENT),
            patch.object(finance.requests, "post", return_value=response) as post,
        ):
            finance._search_tavily_news("AAPL", 2, "fixture-key", 7)
        self.assertEqual(post.call_args.kwargs["json"]["start_date"], "2026-10-02")
        self.assertEqual(post.call_args.kwargs["json"]["end_date"], "2026-10-09")

    def test_old_primary_news_falls_back_to_newer_provider(self):
        response = Mock()
        response.json.return_value = {
            "results": [
                {
                    "title": "Old",
                    "url": "https://news.example/old",
                    "published_date": "2024-01-01",
                }
            ]
        }
        with (
            patch.object(finance, "now_utc", return_value=CURRENT),
            patch.dict(finance.os.environ, {"TAVILY_API_KEY": "fixture-key"}),
            patch.object(finance.requests, "post", return_value=response),
            patch.object(finance, "_fmp_client", None),
            patch.object(
                finance,
                "_public_market_news",
                return_value=[{"title": "Current", "pub_date": "2026-10-08"}],
            ) as rss,
        ):
            result = json.loads(
                finance._make_news_tool().invoke({"query": "AAPL earnings"})
            )
        self.assertEqual(result[0]["title"], "Current")
        self.assertEqual(rss.call_args.kwargs["days"], 7)

    def test_latest_statement_frequency_and_actual_period_are_preserved(self):
        annual = pd.DataFrame({pd.Timestamp("2025-12-31"): [100]}, index=["Revenue"])
        quarterly = pd.DataFrame({pd.Timestamp("2026-06-30"): [0]}, index=["Revenue"])
        ticker = SimpleNamespace(
            financials=annual,
            balance_sheet=annual,
            cashflow=annual,
            quarterly_income_stmt=quarterly,
            quarterly_balance_sheet=quarterly,
            quarterly_cashflow=quarterly,
        )
        with patch(
            "app.providers.ticker_info",
            return_value=(ticker, {"financialCurrency": "USD"}),
        ):
            latest = yf_data.get_financials("AAPL")
            explicit = yf_data.get_financials("AAPL", "annual")
        self.assertEqual(latest["frequency"], "Quarterly")
        self.assertEqual(latest["as_of"], "2026-06-30")
        self.assertEqual(latest["income_statement"]["2026-06-30"]["Revenue"], 0)
        self.assertEqual(explicit["frequency"], "Annual")
        json.dumps(latest)

    def test_statement_response_cache_is_invalidated(self):
        from yfinance.data import YfData
        from app.providers import refresh_statement_cache

        with patch.object(YfData.cache_get, "cache_clear") as clear:
            refresh_statement_cache()
        clear.assert_called_once()

    def test_clock_reaches_parent_and_delegated_specialist(self):
        seen = []

        class RecordingModel(ToolModel):
            def _generate(self, messages, **kwargs):
                seen.extend(m.text for m in messages if isinstance(m, SystemMessage))
                return super()._generate(messages, **kwargs)

        model = RecordingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {
                                "subagent_type": "research",
                                "description": "Gather latest data",
                            },
                            "id": "delegate",
                        }
                    ],
                ),
                AIMessage(content="Provider unavailable; no current data verified."),
                AIMessage(content="No current data verified."),
            ]
        )
        with (
            patch.object(agent, "MODEL_NAME", model),
            patch("orchestrator.freshness.now_utc", return_value=CURRENT),
        ):
            graph = agent.create_agent(None, InMemoryStore())
            graph.invoke(
                {"messages": [HumanMessage(content="Analyze Maybank (1155.KL)")]},
                context=MemoryContext(),
            )
        self.assertGreaterEqual(len(seen), 3)
        self.assertTrue(all("Default research horizon: as of today" in prompt for prompt in seen))
        self.assertTrue(all("Before write_todos or task delegation" in prompt for prompt in seen))
        self.assertTrue(all("explicit historical scope" in prompt for prompt in seen))
        self.assertTrue(all("2026-10-09" in prompt for prompt in seen))
        self.assertTrue(
            all("Previous conversation answers" in prompt for prompt in seen)
        )
