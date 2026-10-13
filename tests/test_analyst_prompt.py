import unittest
from app.prompts import FINANCIAL_ANALYST_PROMPT,SPECIALIST_RESEARCH_PROMPT
from app.subagents import get_subagents_for_type
class AnalystPromptTests(unittest.TestCase):
    def test_parent_requires_delegation_and_calculation_evidence(self):
        for word in ("task", "verified calculation receipts", "/scripts/", "evidence is unavailable"):
            self.assertIn(word,FINANCIAL_ANALYST_PROMPT)
    def test_specialists_receive_shared_instructions(self):
        agents=get_subagents_for_type("general")
        self.assertEqual(len(agents),8)
        for agent in agents:
            self.assertIn(SPECIALIST_RESEARCH_PROMPT,agent["system_prompt"])
            self.assertEqual(agent["skills"],["/skills/"])
