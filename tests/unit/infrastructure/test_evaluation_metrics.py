import unittest
from evaluation.metrics import aggregate
class EvaluationMetricsTests(unittest.TestCase):
    def test_failures_remain_and_p95_uses_nearest_rank(self):
        rows=[{"duration_s": i, "success": i % 2 == 0, "tool_requests": 10,
               "tool_results": 9, "tool_failures": 1, "tokens_used": 100} for i in range(1,21)]
        result=aggregate(rows)
        self.assertEqual(result["runs"],20)
        self.assertEqual(result["completed_reports"],10)
        self.assertEqual(result["p95_duration_s"],19)
        self.assertEqual(result["tool_failure_rate"],0.1)
        self.assertEqual(result["unresolved_tool_requests"],20)
        self.assertIsNone(result["cost_usd"])
    def test_empty_metrics_remain_unavailable(self):
        self.assertIsNone(aggregate([])["p95_duration_s"])
        self.assertIsNone(aggregate([])["tool_failure_rate"])
