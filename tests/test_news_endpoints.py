import json
import os
import unittest
from unittest.mock import Mock, patch
from app import financial_tools as finance
from scripts.data_sources import fmp_client


class NewsEndpointTests(unittest.TestCase):
    def setUp(self):
        rss = patch.object(finance, "_public_market_news", return_value=[])
        rss.start()
        self.addCleanup(rss.stop)
        settings = patch.dict(os.environ, {"TAVILY_API_KEY": ""})
        settings.start()
        self.addCleanup(settings.stop)

    def test_symbol_news_uses_stable_endpoint_and_symbols_parameter(self):
        client = Mock(api_key="test-key")
        client._request.return_value = [{"title": "Verified headline"}]
        with patch.object(finance, "_fmp_client", client):
            data = json.loads(finance._make_news_tool().invoke({"symbol": "0157.kl", "count": 2}))
        client._request.assert_called_once_with("news/stock", {"symbols": "0157.KL", "limit": 2}, version="stable")
        self.assertEqual(data[0]["title"], "Verified headline")

    def test_general_news_uses_latest_endpoint(self):
        client = Mock(api_key="test-key")
        client._request.return_value = []
        with patch.object(finance, "_fmp_client", client):
            data = json.loads(finance._make_news_tool().invoke({"count": 2}))
        client._request.assert_called_once_with("news/stock-latest", {"limit": 2}, version="stable")
        self.assertEqual(data["status"], "empty")

    def test_dispatcher_preserves_auth_and_versioned_urls(self):
        client = fmp_client.FMPClient(api_key="test-only-key")
        response = Mock(status_code=200)
        response.json.return_value = []
        client.session.get = Mock(return_value=response)
        for version, base in [("stable", client.BASE_URL_STABLE), ("v3", client.BASE_URL_V3), ("v4", client.BASE_URL_V4)]:
            client._request("endpoint", {"limit": 2}, version=version)
            client.session.get.assert_called_with(base + "/endpoint", params={"apikey": "test-only-key", "limit": 2}, timeout=client.timeout)
        with self.assertRaises(ValueError):
            client._request("endpoint", version="typo")
        client.session.close()
