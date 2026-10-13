from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch, Mock
from app import financial_tools as finance


class MarketNewsFixTests(unittest.TestCase):
    def setUp(self):
        clock = patch.object(
            finance,
            "now_utc",
            return_value=datetime(2026, 10, 9, 1, 0, tzinfo=timezone.utc),
        )
        clock.start()
        self.addCleanup(clock.stop)

    def response(self, body):
        result = Mock()
        result.iter_content.return_value = [body]
        return result

    def test_general_market_news_works_without_provider_keys(self):
        body = b"<rss><channel><item><title>Market headline</title><link>https://finance.yahoo.com/news/article</link><pubDate>Wed, 07 Oct 2026 08:00:00 GMT</pubDate></item></channel></rss>"
        response = self.response(body)
        with (
            patch.dict(finance.os.environ, {"TAVILY_API_KEY": ""}),
            patch.object(finance, "_fmp_client", None),
            patch.object(finance.requests, "get", return_value=response),
        ):
            result = json.loads(finance._make_news_tool().invoke({"count": 2}))
        self.assertEqual(result[0]["title"], "Market headline")
        self.assertEqual(result[0]["search_provider"], "Google News RSS")
        self.assertEqual(result[0]["pub_date"], "2026-10-07T08:00:00+00:00")
        response.close.assert_called_once()

    def test_feed_rejects_entities_and_keeps_missing_dates_missing(self):
        with patch.object(
            finance.requests, "get", return_value=self.response(b"<!DOCTYPE rss><rss/>")
        ):
            with self.assertRaises(ValueError):
                finance._public_market_news(1)
        body = b"<rss><channel><item><title>Headline</title><link>https://news.example/a</link></item><item><title>Duplicate</title><link>https://news.example/a</link></item></channel></rss>"
        with patch.object(finance.requests, "get", return_value=self.response(body)):
            result = finance._public_market_news(5)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["pub_date"])

    def test_legacy_yahoo_news_accepts_string_publishers(self):
        import yfinance

        provider = Mock(
            news=[
                {
                    "title": "Headline",
                    "publisher": "Publisher",
                    "link": "https://news.example/article",
                    "providerPublishTime": 123,
                }
            ]
        )
        with (
            patch.dict(finance.os.environ, {"TAVILY_API_KEY": ""}),
            patch.object(finance, "_fmp_client", None),
            patch.object(yfinance, "Ticker", return_value=provider),
        ):
            result = json.loads(
                finance._make_news_tool().invoke({"symbol": " aapl ", "days": None})
            )
        self.assertEqual(result[0]["provider"], "Publisher")
        self.assertEqual(result[0]["pub_date"], 123)

    def test_query_is_encoded_and_provider_failures_are_preserved(self):
        body = b"<rss><channel><item><title>Headline</title><link>https://news.example/a</link><source>Reported publisher</source></item></channel></rss>"
        fmp = Mock(api_key="test-only-key")
        fmp._request.side_effect = RuntimeError("provider down")
        with (
            patch.dict(finance.os.environ, {"TAVILY_API_KEY": ""}),
            patch.object(finance, "_fmp_client", fmp),
            patch.object(
                finance.requests, "get", return_value=self.response(body)
            ) as get,
        ):
            result = json.loads(
                finance._make_news_tool().invoke(
                    {"query": "Malaysia markets & earnings", "count": 1}
                )
            )
        self.assertIn("Malaysia+markets+%26+earnings", get.call_args.args[0])
        fmp._request.assert_not_called()
        self.assertEqual(result[0]["provider"], "Reported publisher")
        with (
            patch.dict(finance.os.environ, {"TAVILY_API_KEY": ""}),
            patch.object(finance, "_fmp_client", fmp),
            patch.object(finance.requests, "get", return_value=self.response(body)),
        ):
            fallback = json.loads(finance._make_news_tool().invoke({"count": 1}))
        self.assertEqual(fallback["status"], "partial_success")
        self.assertEqual(fallback["attempts"][0]["provider"], "FMP")
        self.assertEqual(fallback["articles"][0]["provider"], "Reported publisher")
