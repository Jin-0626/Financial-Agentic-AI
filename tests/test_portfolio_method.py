import asyncio, json, os, tempfile, unittest
from unittest.mock import patch
from app import portfolio_service as service, providers
from app.portfolio_repository import MemoryPortfolioRepository
from app.native_engine import NativeEngine


@unittest.skipUnless(
    os.getenv("ENGINE_TEST_COMMAND"),
    "Set ENGINE_TEST_COMMAND for real Rust integration",
)
class NativePortfolioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {
                "ENGINE_COMMAND": os.environ["ENGINE_TEST_COMMAND"],
                "ENGINE_DATA_ROOT": self.temp.name,
            },
        )
        self.env.start()
        self.engine = NativeEngine()
        self.native = patch.object(service, "calculate", self.engine.call)
        self.native.start()
        self.repo = MemoryPortfolioRepository()
        self.doc = await service.create_portfolio(
            self.repo, "org", "user", service.Portfolio()
        )

    async def asyncTearDown(self):
        await self.engine.close()
        self.native.stop()
        self.env.stop()
        self.temp.cleanup()

    def trade(self, id, side, quantity, price, symbol="AAPL"):
        return service.Trade(
            id=id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            date="2025-01-01",
        )

    async def post(self, t, revision=None):
        return await service.record_trade(
            self.repo, "org", "user", self.doc["id"], t, revision
        )

    async def test_weighted_average_sell_dividend_and_idempotency(self):
        await self.post(self.trade("b1", "BUY", "10", "100"))
        await self.post(self.trade("b2", "BUY", "10", "200"))
        sold = await self.post(self.trade("s", "SELL", "5", "180"))
        self.assertEqual(sold["portfolio"]["positions"][0]["average_cost"], "150")
        self.assertEqual(sold["realized_gain"], "150")
        paid = await self.post(self.trade("d", "DIVIDEND", "15", "0.1"))
        self.assertEqual(paid["dividends"], "1.5")
        again = await self.post(self.trade("d", "DIVIDEND", "15", "0.1"))
        self.assertEqual(len(again["transactions"]), 4)
        with self.assertRaises(ValueError):
            await self.post(self.trade("d", "DIVIDEND", "14", "0.1"))

    async def test_oversell_is_atomic_and_full_sell_removes_holding(self):
        await self.post(self.trade("b", "BUY", "0.3", "0.1"))
        with self.assertRaises(ValueError):
            await self.post(self.trade("bad", "SELL", "0.4", "0.2"))
        self.assertEqual(
            len((await self.repo.load("org", "user", self.doc["id"]))["transactions"]),
            1,
        )
        out = await self.post(self.trade("all", "SELL", "0.3", "0.2"))
        self.assertEqual(out["portfolio"]["positions"], [])
        self.assertEqual(out["realized_gain"], "0.03")

    async def test_concurrent_trades_and_revision_conflict(self):
        await asyncio.gather(
            *(self.post(self.trade(str(i), "BUY", "1", "10")) for i in range(6))
        )
        out = await self.repo.load("org", "user", self.doc["id"])
        self.assertEqual(out["portfolio"]["positions"][0]["quantity"], "6")
        with self.assertRaises(ValueError):
            await self.post(self.trade("stale", "BUY", "1", "10"), 1)

    async def test_projection_common_dates_and_currency_rejection(self):
        args = {
            "currency": "USD",
            "benchmark": "SPY",
            "method": "projection",
            "positions": [{"symbol": "AAPL", "quantity": "2", "average_cost": "0"}],
            "series": [
                {
                    "symbol": "AAPL",
                    "currency": "USD",
                    "prices": [
                        {"date": "2025-01-01", "close": "10"},
                        {"date": "2025-01-02", "close": "11"},
                        {"date": "2025-01-03", "close": "12"},
                    ],
                },
                {
                    "symbol": "SPY",
                    "currency": "USD",
                    "prices": [
                        {"date": "2025-01-01", "close": "20"},
                        {"date": "2025-01-03", "close": "21"},
                    ],
                },
            ],
        }
        result = await self.engine.call("org", "portfolio_performance", args)
        self.assertEqual(result["dates"], ["2025-01-01", "2025-01-03"])
        self.assertAlmostEqual(result["portfolio_return_percent"], 20)
        self.assertAlmostEqual(result["benchmark_return_percent"], 5)
        args["series"][0]["currency"] = "MYR"
        with self.assertRaises(ValueError):
            await self.engine.call("org", "portfolio_performance", args)

    async def test_recorded_cash_flow_adjustment(self):
        args = {
            "currency": "USD",
            "benchmark": "SPY",
            "method": "recorded",
            "positions": [],
            "series": [
                {
                    "symbol": "SPY",
                    "currency": "USD",
                    "prices": [
                        {"date": "2025-01-01", "close": "20"},
                        {"date": "2025-01-02", "close": "21"},
                    ],
                }
            ],
            "snapshots": [
                {"date": "2025-01-01", "market_value": "100"},
                {"date": "2025-01-02", "market_value": "160"},
            ],
            "transactions": [
                {
                    "id": "b",
                    "symbol": "AAPL",
                    "side": "BUY",
                    "quantity": "1",
                    "price": "50",
                    "date": "2025-01-02",
                    "notes": "",
                }
            ],
        }
        result = await self.engine.call("org", "portfolio_performance", args)
        self.assertAlmostEqual(result["portfolio_return_percent"], 10)

    async def test_import_roundtrip_merge_conflict_and_holdings_rejection(self):
        data = {
            "format_version": "1.0",
            "portfolio_name": "Imported",
            "currency": "USD",
            "transactions": [
                {
                    "date": "2025-01-01",
                    "symbol": "AAPL",
                    "type": "BUY",
                    "quantity": 2,
                    "price": 100,
                }
            ],
        }
        with patch.object(
            providers, "quote", return_value={"symbol": "AAPL", "currency": "USD"}
        ):
            request = service.ImportRequest(data=data)
            preview = await service.import_preview(
                self.repo, "org", "user", "default", request
            )
            self.assertEqual(len(await self.repo.list("org", "user")), 1)
            request.preview_hash = preview["preview_hash"]
            request.expected_revision = preview["expected_revision"]
            saved = await service.import_commit(
                self.repo, "org", "user", "default", request
            )
            exported = await service.export_json(self.repo, "org", "user", saved["id"])
            merge = service.ImportRequest(mode="Merge", data=exported)
            preview = await service.import_preview(
                self.repo, "org", "user", saved["id"], merge
            )
            merge.preview_hash = preview["preview_hash"]
            merge.expected_revision = preview["expected_revision"]
            again = await service.import_commit(
                self.repo, "org", "user", saved["id"], merge
            )
            self.assertEqual(len(again["transactions"]), 1)
            with self.assertRaises(ValueError):
                await service.import_commit(
                    self.repo, "org", "user", saved["id"], merge
                )
        with self.assertRaises(ValueError):
            service.import_trades({"portfolio_name": "Invalid", "holdings": []})

    async def test_legacy_openings_recovered_without_invented_dates(self):
        old = {
            "positions": [{"symbol": "AAPL", "quantity": "15", "average_cost": "150"}],
            "transactions": [
                {
                    "id": "buy",
                    "symbol": "AAPL",
                    "side": "BUY",
                    "quantity": "10",
                    "price": "200",
                    "date": "2025-01-01",
                    "notes": "",
                    "realized_gain": "0",
                    "total_value": "2000",
                },
                {
                    "id": "sell",
                    "symbol": "AAPL",
                    "side": "SELL",
                    "quantity": "5",
                    "price": "180",
                    "date": "2025-01-01",
                    "notes": "",
                    "realized_gain": "150",
                    "total_value": "900",
                },
            ],
        }
        result = await self.engine.call("org", "recover_opening_positions", old)
        self.assertEqual(result["positions"][0]["quantity"], "10")
        self.assertEqual(result["positions"][0]["average_cost"], "100")

    async def test_registered_native_tool_runs_through_real_specialist(self):
        from agent_fixtures import ToolModel
        from langchain_core.messages import AIMessage, HumanMessage
        from langgraph.store.memory import InMemoryStore
        from orchestrator import agent
        from app.context_type import MemoryContext
        from app.research_tools import current_evidence
        from app.evidence import unwrap
        from datetime import datetime, timezone

        model = ToolModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "id": "delegate",
                            "args": {
                                "description": "Compute risk for AAPL",
                                "subagent_type": "risk-analyzer",
                            },
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "calculate_historical_var",
                            "id": "risk",
                            "args": {
                                "ticker": "AAPL",
                                "lookback_days": 4,
                                "confidence_level": 0.75,
                            },
                        }
                    ],
                ),
                AIMessage(
                    content="Historical one-day VaR is 10%, based on retrieved prices."
                ),
                AIMessage(
                    content="## Executive Summary\n\nHistorical one-day VaR is 10%. It is not a forecast."
                ),
            ]
        )
        snapshot = {
            "schema_version": 1,
            "org_id": "org",
            "ticker": "AAPL",
            "currency": "USD",
            "unit_multiplier": "1",
            "provider": "fixture",
            "source_reference": "reviewed fixture",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "as_of": "2025-01-05",
            "dataset": {
                "kind": "prices",
                "bars": [
                    {"date": f"2025-01-0{i + 1}", "adjusted_close": price, "volume": 0}
                    for i, price in enumerate([100, 110, 99, 108.9, 98.01])
                ],
            },
        }
        tools = await self.engine.agent_tools("org")
        with (
            patch.object(agent, "MODEL_NAME", model),
            patch(
                "orchestrator.tools.native_snapshots.fetch_yahoo_snapshot",
                return_value=snapshot,
            ),
        ):
            graph = agent.create_agent(None, InMemoryStore(), native_tools=tools)
            result = await graph.ainvoke(
                {"messages": [HumanMessage(content="Research AAPL risk")]},
                context=MemoryContext(org_id="org"),
            )
        evidence = current_evidence(result["messages"])
        self.assertEqual(evidence[0].name, "calculate_historical_var")
        payload, meta = unwrap(evidence[0])
        self.assertAlmostEqual(payload["data"]["historical"]["var"], 0.1)
        self.assertIsNotNone(meta)
        self.assertNotIn("payload_hash", result["messages"][-1].content)

    async def test_trade_correction_replays_rust_and_preserves_original(self):
        await self.post(self.trade("buy", "BUY", "10", "100"))
        await self.post(self.trade("sell", "SELL", "5", "180"))
        before = await self.repo.load("org", "user", self.doc["id"])
        edited = await service.correct_trade(
            self.repo,
            "org",
            "user",
            self.doc["id"],
            "buy",
            service.TradeCorrection(
                quantity="8", price="110", date="2025-01-01", notes="Correction"
            ),
            before["revision"],
        )
        self.assertEqual(edited["portfolio"]["positions"][0]["quantity"], "3")
        self.assertEqual(edited["portfolio"]["positions"][0]["average_cost"], "110")
        self.assertEqual(edited["realized_gain"], "350")
        self.assertEqual(
            edited["transaction_corrections"][0]["previous"]["quantity"], "10"
        )
        self.assertEqual([t["id"] for t in edited["transactions"]], ["buy", "sell"])
        self.assertTrue(edited["performance_reset_date"])
        with self.assertRaisesRegex(ValueError, "reload"):
            await service.correct_trade(
                self.repo,
                "org",
                "user",
                self.doc["id"],
                "buy",
                service.TradeCorrection(quantity="9", price="110", date="2025-01-01"),
                before["revision"],
            )

    async def test_invalid_trade_correction_is_atomic_and_noop_does_not_write(self):
        await self.post(self.trade("buy", "BUY", "10", "100"))
        await self.post(self.trade("sell", "SELL", "5", "180"))
        before = await self.repo.load("org", "user", self.doc["id"])
        for correction in (
            service.TradeCorrection(quantity="4", price="100", date="2025-01-01"),
            service.TradeCorrection(quantity="10", price="100", date="2025-01-02"),
        ):
            with self.assertRaises(ValueError):
                await service.correct_trade(
                    self.repo,
                    "org",
                    "user",
                    self.doc["id"],
                    "buy",
                    correction,
                    before["revision"],
                )
            self.assertEqual(
                await self.repo.load("org", "user", self.doc["id"]), before
            )
        same = await service.correct_trade(
            self.repo,
            "org",
            "user",
            self.doc["id"],
            "buy",
            service.TradeCorrection(quantity="10", price="100", date="2025-01-01"),
            before["revision"],
        )
        self.assertEqual(same, before)
        with self.assertRaises(ValueError):
            await service.correct_trade(
                self.repo,
                "other",
                "user",
                self.doc["id"],
                "buy",
                service.TradeCorrection(quantity="10", price="100", date="2025-01-01"),
                before["revision"],
            )
