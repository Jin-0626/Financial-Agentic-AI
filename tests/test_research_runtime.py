import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import financial_tools as finance
from app import routes
from app.subagents import get_subagents_for_type
from app.research_integrity import RESEARCH_INTEGRITY


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        rss = patch.object(finance, "_public_market_news", return_value=[])
        rss.start()
        self.addCleanup(rss.stop)
        settings = patch.dict(os.environ, {"TAVILY_API_KEY": ""})
        settings.start()
        self.addCleanup(settings.stop)


    def test_market_tool_independent_of_sandbox(self):
        provider = Mock()
        provider.get_quote.return_value = {"symbol": "0157.KL", "price": 0.5}
        with patch.object(finance, "stock_data", provider):
            result = json.loads(finance._make_market_data_tool().invoke({"symbol": "0157.KL"}))
            self.assertEqual(result["price"], 0.5)

    def test_news_empty_and_failed_are_distinct(self):
        import yfinance
        with patch.object(finance, "_fmp_client", None), patch.object(yfinance, "Ticker", return_value=SimpleNamespace(news=[])):
            result = json.loads(finance._make_news_tool().invoke({"symbol": "0157.KL"}))
            self.assertEqual(result["status"], "empty")
            self.assertNotIn("error", result)
        with patch.object(finance, "_fmp_client", None), patch.object(yfinance, "Ticker", side_effect=RuntimeError("HTTP 429")):
            result = json.loads(finance._make_news_tool().invoke({"symbol": "0157.KL"}))
            self.assertEqual(result["status"], "error")
            self.assertIn("429", result["attempts"][0]["error"])

    def test_news_fallback_is_independent_of_analytics_import(self):
        import yfinance
        fmp = Mock(api_key="configured")
        fmp._request.side_effect = RuntimeError("401")
        with patch.object(finance, "stock_data", None), patch.object(finance, "_fmp_client", fmp), patch.object(yfinance, "Ticker", return_value=SimpleNamespace(news=[{"content": {"title": "Verified headline"}}])):
            result = json.loads(finance._make_news_tool().invoke({"symbol": "0157.KL"}))
            self.assertEqual(result["articles"][0]["title"], "Verified headline")
            self.assertEqual(result["status"], "partial_success")

    def test_retired_status_and_provider_context(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(routes.retired_sandbox())
        self.assertEqual(raised.exception.status_code, 410)
        self.assertIn("retired", routes._build_env_footer("one"))
        self.assertIn("independently", routes._build_env_footer("one"))

    def test_specialists_have_skills_and_shared_evidence_rules(self):
        agents = get_subagents_for_type("general")
        self.assertEqual(len(agents), 8)
        for agent in agents:
            self.assertEqual(agent["skills"], ["/skills/"])
            self.assertNotIn("tools", agent)  # inherit parent tools
            self.assertIn(RESEARCH_INTEGRITY, agent["system_prompt"])

    def test_specialist_inherits_market_tool_in_real_graph(self):
        from deepagents import create_deep_agent
        from deepagents.backends import StateBackend
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
        from langchain_core.messages import AIMessage
        class ToolModel(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "task", "args": {"description": "Retrieve quote and report errors", "subagent_type": "research"}, "id": "delegate"}]),
            AIMessage(content="", tool_calls=[{"name": "market_data", "args": {"symbol": "0157.KL"}, "id": "quote"}]),
            AIMessage(content="Provider returned HTTP 429; current valuation is unavailable."),
            AIMessage(content="market_data failed with HTTP 429; no company narrative is verified."),
        ])
        provider = Mock()
        provider.get_quote.return_value = {"error": "HTTP 429"}
        with patch.object(finance, "stock_data", provider):
            graph = create_deep_agent(
                model=model, system_prompt=RESEARCH_INTEGRITY,
                tools=finance.build_financial_tools(),
                subagents=[get_subagents_for_type("general")[0]], backend=StateBackend(),
            )
            result = graph.invoke({"messages": [{"role": "user", "content": "Research Focus Point"}]})
        provider.get_quote.assert_called_once_with("0157.KL")
        self.assertIn("HTTP 429", result["messages"][-1].content)


if __name__ == "__main__":
    unittest.main()
