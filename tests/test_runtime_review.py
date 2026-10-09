import json
import unittest
from unittest.mock import patch, Mock
import pandas as pd
from app import financial_tools

class RuntimeReviewTests(unittest.TestCase):



    def test_ollama_endpoint_is_used_and_model_objects_are_preserved(self):
        from orchestrator import agent
        from langchain_ollama import ChatOllama
        with patch.object(agent,"MODEL_NAME","ollama:test-model"), patch.object(agent,"OLLAMA_URL","http://127.0.0.1:11435"):
            model=agent._configured_model()
            self.assertIsInstance(model,ChatOllama)
            self.assertEqual(model.base_url,"http://127.0.0.1:11435")
            self.assertEqual(model.model,"test-model")
        fake=Mock()
        with patch.object(agent,"MODEL_NAME",fake),patch.object(agent,"OLLAMA_URL","https://ollama.com"):
            self.assertIs(agent._configured_model(),fake)
        with patch.object(agent,"MODEL_NAME","ollama:test-model"),patch.object(agent,"OLLAMA_URL","file:///private"):
            with self.assertRaises(ValueError): agent._configured_model()






