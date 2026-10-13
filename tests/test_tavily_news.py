from datetime import datetime, timezone
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import requests
import yfinance
from app import financial_tools as finance


class TavilyNewsTests(unittest.TestCase):
    def setUp(self):
        clock = patch.object(
            finance,
            "now_utc",
            return_value=datetime(2026, 10, 9, 1, 0, tzinfo=timezone.utc),
        )
        clock.start()
        self.addCleanup(clock.stop)

        rss = patch.object(finance, "_public_market_news", return_value=[])
        rss.start()
        self.addCleanup(rss.stop)
        for setting in [
            patch.dict(os.environ, {"TAVILY_API_KEY": "test-tavily-secret"}),
            patch.object(finance, "_fmp_client", None),
            patch.object(yfinance, "Ticker", return_value=SimpleNamespace(news=[])),
        ]:
            setting.start()
            self.addCleanup(setting.stop)

    def response(self, payload):
        response = Mock()
        response.json.return_value = payload
        return response

    def invoke(self, **kwargs):
        return json.loads(
            finance._make_news_tool().invoke({"symbol": "0157.KL", **kwargs})
        )

    def test_primary_search_preserves_article_evidence(self):
        response = self.response(
            {
                "results": [
                    {
                        "title": "Reported results",
                        "url": "https://news.example/report",
                        "content": "Article excerpt",
                        "published_date": "2026-10-08",
                    }
                ],
                "answer": "Never use this generated answer",
            }
        )
        with patch.object(finance.requests, "post", return_value=response) as post:
            result = self.invoke(query="Focus Point Holdings earnings", count=2)
        args = post.call_args.kwargs
        self.assertEqual(args["headers"]["Authorization"], "Bearer test-tavily-secret")
        self.assertEqual(args["json"]["topic"], "news")
        self.assertEqual(args["json"]["max_results"], 2)
        self.assertIn("0157.KL", args["json"]["query"])
        self.assertIn("Focus Point", args["json"]["query"])
        self.assertFalse(args["json"]["include_answer"])
        self.assertEqual(result[0]["pub_date"], "2026-10-08")
        self.assertEqual(result[0]["provider"], "news.example")
        self.assertEqual(result[0]["search_provider"], "Tavily")
        self.assertNotIn("Never use", json.dumps(result))
        response.close.assert_called_once()

    def test_empty_search_is_distinct_from_failure(self):
        with patch.object(
            finance.requests, "post", return_value=self.response({"results": []})
        ):
            result = self.invoke()
        self.assertEqual(result["status"], "empty")
        self.assertEqual(
            result["attempts"][0], {"provider": "Tavily", "status": "empty"}
        )

    def test_missing_key_does_not_make_http_request(self):
        with (
            patch.dict(os.environ, {"TAVILY_API_KEY": ""}),
            patch.object(finance.requests, "post") as post,
        ):
            result = self.invoke()
        post.assert_not_called()
        self.assertEqual(result["attempts"][-1]["status"], "unavailable")

    def test_error_retained_when_fallback_succeeds(self):
        client = Mock(api_key="fake")
        client._request.return_value = [{"title": "Fallback headline"}]
        with (
            patch.object(
                finance.requests,
                "post",
                side_effect=requests.Timeout("timeout test-tavily-secret"),
            ),
            patch.object(finance, "_fmp_client", client),
        ):
            result = self.invoke()
        self.assertEqual(result["status"], "partial_success")
        self.assertEqual(result["articles"][0]["title"], "Fallback headline")
        self.assertIn("[redacted]", result["attempts"][0]["error"])
        self.assertNotIn("test-tavily-secret", json.dumps(result))

    def test_http_auth_failure_is_not_an_empty_search(self):
        response = self.response({})
        response.raise_for_status.side_effect = requests.HTTPError("401 Unauthorized")
        with patch.object(finance.requests, "post", return_value=response):
            result = self.invoke()
        self.assertEqual(result["status"], "error")
        self.assertIn("401", result["attempts"][0]["error"])
        response.close.assert_called_once()

    def test_malformed_results_fail_explicitly(self):
        for payload in [
            {"error": "quota"},
            {"results": "invalid"},
            {"results": [{"title": "Missing URL"}]},
        ]:
            with (
                self.subTest(payload=payload),
                patch.object(
                    finance.requests, "post", return_value=self.response(payload)
                ),
            ):
                self.assertEqual(self.invoke()["status"], "error")

    def test_missing_publication_date_is_not_invented(self):
        response = self.response(
            {"results": [{"title": "News", "url": "https://news.example/article"}]}
        )
        with patch.object(finance.requests, "post", return_value=response):
            self.assertIsNone(self.invoke()[0]["pub_date"])

    def test_count_validation_prevents_provider_call(self):
        with patch.object(finance.requests, "post") as post:
            self.assertEqual(self.invoke(count=21)["category"], "validation")
        post.assert_not_called()
