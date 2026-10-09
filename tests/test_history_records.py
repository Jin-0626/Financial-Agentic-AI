"""Public history contracts share conversion without losing field differences."""

import unittest
from unittest.mock import Mock, patch
import pandas as pd
from app import financial_tools


class HistoryRecordsTests(unittest.TestCase):
    def test_both_history_modes_preserve_values_and_filter_invalid_closes(self):
        stock = financial_tools.stock_data
        frame = pd.DataFrame(
            {
                "Open": [1.0, 2.0],
                "High": [1.5, 2.5],
                "Low": [0.5, 1.5],
                "Close": [1.25, float("nan")],
                "Volume": [100, 200],
            },
            index=pd.date_range("2025-01-01", periods=2, tz="UTC"),
        )
        ticker = Mock()
        ticker.history.return_value = frame
        with patch.object(stock.yf, "Ticker", return_value=ticker):
            dated = stock.get_historical("AAPL", "2025-01-01", "2025-01-03")
            period = stock.get_historical_period("AAPL", period="1mo")
        expected = {
            "timestamp": 1735689600,
            "date": "2025-01-01",
            "open": 1.0,
            "high": 1.5,
            "low": 0.5,
            "close": 1.25,
            "volume": 100,
        }
        self.assertEqual(period, [expected])
        self.assertEqual(dated, [{"symbol": "AAPL", **expected}])
        self.assertEqual(ticker.history.call_args_list[0].kwargs["start"], "2025-01-01")
        self.assertEqual(ticker.history.call_args_list[1].kwargs["period"], "1mo")

    def test_empty_and_failed_histories_preserve_public_contract(self):
        stock = financial_tools.stock_data
        ticker = Mock()
        ticker.history.return_value = pd.DataFrame()
        with patch.object(stock.yf, "Ticker", return_value=ticker):
            self.assertEqual(stock.get_historical_period("AAPL"), [])
            ticker.history.side_effect = RuntimeError("Provider unavailable")
            self.assertEqual(
                stock.get_historical("AAPL", "2025-01-01", "2025-01-03"),
                [{"error": "Provider unavailable", "symbol": "AAPL"}],
            )


if __name__ == "__main__":
    unittest.main()


class PersistedConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_reconstructs_messages_and_ignores_specialist_namespaces(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from langchain_core.messages import HumanMessage, AIMessage
        from app import routes

        thread = "org__user__thread"

        async def checkpoints(*args, **kwargs):
            for namespace in ("task:specialist", ""):
                yield SimpleNamespace(
                    config={
                        "configurable": {
                            "thread_id": thread,
                            "checkpoint_ns": namespace,
                        }
                    },
                    checkpoint={"channel_values": {"messages": object()}},
                )

        saver = SimpleNamespace(alist=checkpoints)
        graph = SimpleNamespace(
            aget_state=AsyncMock(
                return_value=SimpleNamespace(
                    values={
                        "messages": [
                            HumanMessage(content="Saved question"),
                            AIMessage(content="Saved report"),
                        ]
                    }
                )
            )
        )
        with (
            patch.object(routes, "_checkpointer", saver),
            patch.object(routes, "_agent", graph),
        ):
            found = await routes._list_threads("org", "user")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].last_message, "Saved question")
        self.assertEqual(
            graph.aget_state.await_args.args[0]["configurable"]["checkpoint_ns"], ""
        )
        graph.aget_state.assert_awaited_once()
