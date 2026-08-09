from nodes import (
    _compact_raw_data,
    _financial_forecast_markdown,
    _invoke_llm,
    analysis_agent_node,
    bear_agent_node,
    bull_agent_node,
    debate_agent_node,
    judge_agent_node,
    modeling_agent_node,
    planner_agent_node,
    report_agent_node,
    replanner_agent_node,
    synthesis_agent_node,
)

import pytest

pytestmark = pytest.mark.failure


def test_nodes_with_empty_state_keys():
  """Failure Case: Ensure nodes do not throw KeyError when raw_data is empty or malformed."""

  # Simulate a corrupted/empty state passed to downstream nodes
  corrupted_state = {
      "ticker": "INVALID.KL",
      "company_name": "Test Company",
      "raw_data": {},  # Missing 'fundamentals' sub-dictionary
      "valuation_model": {},
      "bull_case": "N/A",
      "bear_case": "N/A",
  }

  modeling_output = modeling_agent_node(corrupted_state)
  assert modeling_output["workflow_status"] == "FAILED"
  assert modeling_output["failed_stage"] == "modeling_agent"
  assert "current price is unavailable" in modeling_output["error_message"]

  # 2. Test Report Node with empty raw_data
  # Should render 'N/A' strings instead of throwing KeyError / TypeError
  report_output = report_agent_node(corrupted_state)
  assert "final_report" in report_output
  assert "No investment recommendation was generated" in report_output["final_report"]


def test_report_agent_does_not_relabel_upstream_failure():
  state = {
      "ticker": "5275.KL",
      "workflow_status": "FAILED",
      "failed_stage": "synthesis_agent",
      "error_message": "synthesis_agent returned an empty LLM response.",
      "node_metrics": [],
      "judge_verdict": {},
  }

  output = report_agent_node(state)

  assert output["workflow_status"] == "FAILED"
  assert "Failed stage: synthesis_agent" in output["final_report"]
  assert output["audit_trace"] == [
      "INFO [report_agent]: Generated failure report for synthesis_agent."
  ]
  assert "FAILED [report_agent]" not in output["audit_trace"][0]


def test_synthesis_uses_primary_model_directly(monkeypatch):
  class Response:
    def __init__(self, content):
      self.content = content
      self.usage_metadata = {"input_tokens": 10, "output_tokens": 5}

  class FakeFastLLM:
    model = "fast-test"

    def invoke(self, prompt):
      raise AssertionError("synthesis_agent should not call fast_llm")

  class FakeHeavyLLM:
    model = "heavy-test"

    def invoke(self, prompt):
      return Response("Validated synthesis")

  monkeypatch.setattr("nodes.fast_llm", FakeFastLLM())
  monkeypatch.setattr("nodes.heavy_llm", FakeHeavyLLM())

  output = synthesis_agent_node(
      {
          "ticker": "5275.KL",
          "raw_data": {"fundamentals": {"current_price": 1.0}},
          "financial_metrics": {"analysis_notes": "ok"},
          "valuation_model": {"estimated_fair_value_myr": 1.2},
      }
  )

  assert output["baseline_thesis"] == "Validated synthesis"
  assert output["node_metrics"][0]["status"] == "success"
  assert output["node_metrics"][0]["fallback_used"] is False
  assert output["audit_trace"] == [
      "SUCCESS [synthesis_agent]: Compiled baseline research dossier."
  ]


def test_analysis_agent_is_deterministic_and_does_not_call_llm(monkeypatch):
  class FailingLLM:
    model = "should-not-run"

    def invoke(self, prompt):
      raise AssertionError("analysis_agent should not call an LLM")

  monkeypatch.setattr("nodes.heavy_llm", FailingLLM())
  output = analysis_agent_node(
      {
          "ticker": "0157.KL",
          "raw_data": {
              "fundamentals": {
                  "current_price": 0.88,
                  "pe_ratio": 12.5,
                  "dividend_yield": 2.1,
                  "sector": "Consumer Cyclical",
              },
              "quarterly_reports": {
                  "source": "yfinance",
                  "status": "SUCCESS",
              },
          },
      }
  )

  assert output["financial_metrics"]["pe_ratio"] == 12.5
  assert output["financial_metrics"]["div_yield"] == 2.1
  assert output["financial_metrics"]["source"] == "deterministic"
  assert output["node_metrics"][0]["llm_calls"] == 0


def test_planner_agent_builds_orchestration_plan_without_llm():
  output = planner_agent_node(
      {
          "ticker": "0157.KL",
          "company_name": "Focus Point Holdings Berhad",
          "raw_data": {
              "fundamentals": {
                  "symbol": "0157.KL",
                  "company_name": "Focus Point Holdings Berhad",
                  "current_price": 0.825,
                  "pe_ratio": 10.5,
              },
              "quarterly_reports": {
                  "source": "yfinance",
                  "status": "SUCCESS",
                  "quarterly_financials": {"2026-03-31": {"Total Revenue": 59_700_000}},
              },
          },
      }
  )

  plan = output["research_plan"]

  assert plan["data_quality"] == "complete"
  assert plan["debate_required"] is True
  assert "data_agent" in plan["required_agents"]
  assert "analysis_agent" in plan["required_agents"]
  assert "debate_agent" in plan["required_agents"]
  assert "replanner_agent" in plan["required_agents"]
  assert "judge_agent" in plan["required_agents"]
  assert output["node_metrics"][0]["llm_calls"] == 0
  assert output["node_metrics"][0]["metadata"]["planned_agents"] == 10


def test_planner_agent_can_start_before_data_agent():
  output = planner_agent_node(
      {
          "ticker": "0157.KL",
          "debate_rounds": 0,
          "audit_trace": [],
          "node_metrics": [],
          "errors": [],
      }
  )

  plan = output["research_plan"]

  assert plan["company_name"] == "0157.KL"
  assert plan["data_quality"] == "partial"
  assert "data_agent" in plan["required_agents"]
  assert "Market and quarterly data pending" in plan["data_gaps"][0]


def test_planner_agent_downgrades_after_data_agent_double_check_mismatch():
  output = planner_agent_node(
      {
          "ticker": "5275.KL",
          "company_name": "Mynews Holdings Berhad",
          "raw_data": {
              "fundamentals": {
                  "symbol": "5275.KL",
                  "company_name": "Mynews Holdings Berhad",
                  "current_price": 0.43,
                  "pe_ratio": 21.5,
              },
              "quarterly_reports": {
                  "source": "yfinance",
                  "status": "SUCCESS",
                  "quarterly_financials": {"2026-04-30": {"Total Revenue": 225_967_000}},
                  "data_quality_review": {
                      "status": "MISMATCH",
                      "mismatches": [
                          {
                              "period": "2026-04-30",
                              "field": "Total Revenue",
                              "primary_value": 225_967_000,
                              "secondary_value": 180_000_000,
                          }
                      ],
                  },
              },
          },
      }
  )

  plan = output["research_plan"]

  assert plan["data_quality"] == "degraded"
  assert "double-check found 1 field mismatch" in plan["data_gaps"][0]
  assert "verify against Bursa filings" in plan["recovery_actions"][0]


def test_modeling_agent_empty_llm_response_keeps_dcf_with_fallback(monkeypatch):
  class EmptyLLM:
    model = "empty-test"

    def invoke(self, prompt):
      return ""

  monkeypatch.setattr("nodes.heavy_llm", EmptyLLM())
  monkeypatch.setattr("nodes.fast_llm", EmptyLLM())
  output = modeling_agent_node(
      {
          "ticker": "0157.KL",
          "raw_data": {
              "fundamentals": {
                  "current_price": 0.505,
                  "pe_ratio": 8.42,
              }
          },
      }
  )

  assert "workflow_status" not in output
  assert output["valuation_model"]["status"] == "SUCCESS"
  assert output["valuation_model"]["summary_source"] == "deterministic_fallback"
  assert "modeling_agent returned an empty LLM response" in output["valuation_model"]["summary_fallback_reason"]
  assert output["node_metrics"][0]["status"] == "fallback"
  assert output["node_metrics"][0]["fallback_used"] is True
  assert any(trace.startswith("FALLBACK [modeling_agent]") for trace in output["audit_trace"])


def test_llm_interceptor_retries_and_downgrades():
  class Response:
    def __init__(self, content):
      self.content = content
      self.usage_metadata = {"input_tokens": 4, "output_tokens": 2}

  class FailingLLM:
    model = "primary"

    def invoke(self, prompt):
      raise TimeoutError("temporary outage")

  class FastLLM:
    model = "fast"

    def __init__(self):
      self.prompt_seen = ""

    def invoke(self, prompt):
      self.prompt_seen = prompt
      return Response("downgraded answer")

  fast = FastLLM()

  content, metric = _invoke_llm(
      "test_agent",
      FailingLLM(),
      "x" * 7000,
      downgrade_llm=fast,
      max_retries=1,
  )

  assert content == "downgraded answer"
  assert metric["downgraded"] is True
  assert metric["retry_count"] == 2
  assert metric["metadata"]["compressed"] is True
  assert "[CONTENT COMPRESSED:" in fast.prompt_seen


def test_bull_agent_empty_llm_response_uses_deterministic_fallback(monkeypatch):
  class EmptyLLM:
    model = "empty-test"

    def invoke(self, prompt):
      return ""

  monkeypatch.setattr("nodes.heavy_llm", EmptyLLM())
  output = bull_agent_node(
      {
          "ticker": "0157.KL",
          "company_name": "Focus Point Holdings Berhad",
          "raw_data": {
              "fundamentals": {
                  "company_name": "Focus Point Holdings Berhad",
                  "current_price": 0.505,
                  "pe_ratio": 8.42,
                  "dividend_yield": 5.94,
                  "sector": "Healthcare",
              }
          },
          "valuation_model": {
              "estimated_fair_value_myr": 0.67,
              "upside_downside_pct": 32.7,
          },
          "baseline_thesis": "Focus Point is undervalued with dividend support.",
          "research_plan": {"data_quality": "complete"},
          "debate_rounds": 0,
      }
  )

  assert "workflow_status" not in output
  assert "Bull thesis" in output["bull_case"]
  assert output["node_metrics"][0]["status"] == "fallback"
  assert output["node_metrics"][0]["fallback_used"] is True
  assert output["audit_trace"][0].startswith("FALLBACK [bull_agent]")


def test_bull_fallback_handles_negative_dcf_upside_without_contradiction(monkeypatch):
  class EmptyLLM:
    model = "empty-test"

    def invoke(self, prompt):
      return ""

  monkeypatch.setattr("nodes.heavy_llm", EmptyLLM())
  output = bull_agent_node(
      {
          "ticker": "4677.KL",
          "company_name": "YTL Corporation Berhad",
          "raw_data": {
              "fundamentals": {
                  "company_name": "YTL Corporation Berhad",
                  "current_price": 2.12,
                  "pe_ratio": 16.31,
                  "dividend_yield": 2.36,
                  "sector": "Utilities",
              }
          },
          "valuation_model": {
              "estimated_fair_value_myr": 1.82,
              "upside_downside_pct": -14.16,
          },
          "baseline_thesis": "Price appears stretched versus fair value.",
          "research_plan": {"data_quality": "complete"},
          "debate_rounds": 0,
      }
  )

  assert "valuation deficit" in output["bull_case"]
  assert "improving enough" in output["bull_case"]
  assert "Re-rating toward fair value would imply -14" not in output["bull_case"]
  assert output["node_metrics"][0]["status"] == "fallback"


def test_bear_agent_empty_llm_response_uses_deterministic_fallback(monkeypatch):
  class EmptyLLM:
    model = "empty-test"

    def invoke(self, prompt):
      return ""

  monkeypatch.setattr("nodes.heavy_llm", EmptyLLM())
  output = bear_agent_node(
      {
          "ticker": "0157.KL",
          "company_name": "Focus Point Holdings Berhad",
          "raw_data": {
              "fundamentals": {
                  "company_name": "Focus Point Holdings Berhad",
                  "current_price": 0.505,
                  "pe_ratio": 8.42,
                  "dividend_yield": 5.94,
                  "sector": "Healthcare",
              }
          },
          "valuation_model": {"estimated_fair_value_myr": 0.67},
          "baseline_thesis": "Focus Point is undervalued with dividend support.",
          "research_plan": {"data_quality": "complete"},
      }
  )

  assert "workflow_status" not in output
  assert "Bear thesis" in output["bear_case"]
  assert output["node_metrics"][0]["status"] == "fallback"
  assert output["node_metrics"][0]["fallback_used"] is True
  assert output["audit_trace"][0].startswith("FALLBACK [bear_agent]")


def test_debate_agent_structures_bull_bear_cases_before_judge():
  output = debate_agent_node(
      {
          "ticker": "0157.KL",
          "company_name": "Focus Point Holdings Berhad",
          "bull_case": "Bull case says valuation upside and dividend support are attractive.",
          "bear_case": "Bear case says the DCF proxy may overstate fair value.",
          "raw_data": {"fundamentals": {"current_price": 0.505, "dividend_yield": 5.94}},
          "valuation_model": {"estimated_fair_value_myr": 0.84},
      }
  )

  brief = output["debate_brief"]

  assert "bull_summary" in brief
  assert "bear_summary" in brief
  assert len(brief["contested_points"]) >= 1
  assert len(brief["decision_questions"]) >= 1
  assert output["node_metrics"][0]["llm_calls"] == 0
  assert output["audit_trace"] == [
      "SUCCESS [debate_agent]: Structured bull/bear debate before judge."
  ]


def test_replanner_updates_plan_after_degraded_debate_node():
  output = replanner_agent_node(
      {
          "ticker": "0157.KL",
          "research_plan": {
              "data_quality": "complete",
              "replanning_events": [],
              "recovery_actions": [],
          },
          "bull_case": "Bull case is available.",
          "bear_case": "Bear case is available from deterministic fallback.",
          "node_metrics": [
              {
                  "node": "bear_agent",
                  "status": "fallback",
                  "fallback_used": True,
              }
          ],
      }
  )

  assert output["research_plan"]["data_quality"] == "partial"
  assert output["replan_decision"]["triggered"] is True
  assert output["replan_decision"]["judge_can_continue"] is True
  assert "Detected degraded node output from bear_agent." in output["research_plan"]["replanning_events"]
  assert output["audit_trace"][0].startswith("REPLANNED [replanner_agent]")
  assert output["node_metrics"][0]["llm_calls"] == 0


def test_replanner_blocks_judge_when_debate_inputs_missing():
  output = replanner_agent_node(
      {
          "ticker": "0157.KL",
          "research_plan": {"data_quality": "complete"},
          "bull_case": "",
          "bear_case": "Bear case exists.",
          "node_metrics": [],
      }
  )

  assert output["workflow_status"] == "FAILED"
  assert output["failed_stage"] == "replanner_agent"
  assert "judge_agent" in output["error_message"]


def test_report_agent_uses_broker_note_structure():
  state = {
      "ticker": "0157.KL",
      "company_name": "Focus Point Holdings Berhad",
      "workflow_status": "SUCCESS",
      "raw_data": {
          "fundamentals": {
              "symbol": "0157.KL",
              "source": "yfinance",
              "status": "SUCCESS",
              "company_name": "Focus Point Holdings Berhad",
              "sector": "Consumer Cyclical",
              "industry": "Specialty Retail",
              "current_price": 0.825,
              "pe_ratio": 10.5,
              "forward_pe": 9.2,
              "dividend_yield": 3.6,
              "fifty_two_week_low": 0.52,
              "fifty_two_week_high": 1.02,
              "market_cap": 381_000_000,
              "summary": "Focus Point owns and operates eye care centres and F&B bakery chain Komugi.",
          },
          "quarterly_reports": {
              "source": "yfinance",
              "status": "SUCCESS",
              "quarterly_financials": {
                  "2026-03-31": {
                      "Total Revenue": 59_700_000,
                      "Operating Income": 9_400_000,
                      "Net Income": 6_000_000,
                  }
              },
          },
      },
      "financial_metrics": {"analysis_notes": "ok"},
      "valuation_model": {
          "status": "SUCCESS",
          "estimated_fair_value_myr": 1.20,
          "pe_ratio_used": 10.5,
      },
      "baseline_thesis": "Optical sales remain resilient with store expansion.",
      "bull_case": "Corporate sales and store rollout can support growth.",
      "bear_case": "Consumer slowdown could pressure discretionary demand.",
      "judge_verdict": {
          "valid": True,
          "target_price_myr": 1.20,
          "raw_judgement": (
              "Recommendation: BUY\n"
              "Entry Price: RM 0.825\n"
              "Target Price: RM 1.20\n"
              "Stop-Loss: RM 0.70\n"
              "Confidence: Medium\n"
              "Rationale: valuation upside."
          ),
      },
      "node_metrics": [],
  }

  output = report_agent_node(state)
  report = output["final_report"]

  assert output["workflow_status"] == "SUCCESS"
  assert "## Investment Call" in report
  assert "## Company Snapshot" in report
  assert "## Financial Forecast" in report
  assert "## Recent Quarterly Actuals" in report
  assert "## Rating Guide" in report
  assert "| BUY | RM 1.20 | RM 0.825" in report
  assert "Focus Point owns and operates eye care centres" in report
  assert "Forecast is model-derived from recent quarterly actuals" in report
  assert "| Revenue | 59.7 |" in report
  assert "| Net income | 6.0 |" in report


def test_financial_forecast_uses_future_estimates_not_historical_actual_columns():
  quarterly_financials = {
      "2026-04-30T00:00:00": {
          "Total Revenue": 225_967_000,
          "EBITDA": 27_979_000,
          "Operating Income": 26_035_000,
          "Net Income": 55_000,
      },
      "2026-01-31T00:00:00": {
          "Total Revenue": 238_438_000,
          "EBITDA": 32_695_000,
          "Operating Income": 30_582_000,
          "Net Income": 4_069_000,
      },
      "2025-10-31T00:00:00": {
          "Total Revenue": 229_662_000,
          "EBITDA": 32_337_000,
          "Operating Income": 30_886_000,
          "Net Income": 5_265_000,
      },
      "2025-07-31T00:00:00": {
          "Total Revenue": 230_940_000,
          "EBITDA": 34_242_000,
          "Operating Income": 32_305_000,
          "Net Income": 6_426_000,
      },
  }

  forecast = _financial_forecast_markdown(quarterly_financials)

  assert "Forecast is model-derived" in forecast
  assert "2026-07-31" in forecast
  assert "2026-10-31" in forecast
  assert "2026-04-30" not in forecast
  assert "| Revenue | 224.5 |" in forecast
  assert "capped at -0.7%" in forecast
  assert "recent actual trend" in forecast


def test_financial_amount_keeps_precision_for_sub_million_values():
  state = {
      "ticker": "5275.KL",
      "company_name": "Mynews Holdings Berhad",
      "workflow_status": "SUCCESS",
      "raw_data": {
          "fundamentals": {
              "symbol": "5275.KL",
              "source": "yfinance",
              "status": "SUCCESS",
              "company_name": "Mynews Holdings Berhad",
              "sector": "Consumer Cyclical",
              "industry": "Specialty Retail",
              "current_price": 0.43,
              "pe_ratio": 21.5,
              "dividend_yield": 3.49,
          },
          "quarterly_reports": {
              "source": "yfinance",
              "status": "SUCCESS",
              "quarterly_financials": {
                  "2026-04-30T00:00:00": {
                      "Total Revenue": 225_967_000,
                      "Net Income": 55_000,
                  }
              },
          },
      },
      "valuation_model": {"status": "SUCCESS", "estimated_fair_value_myr": 0.28, "pe_ratio_used": 21.5},
      "baseline_thesis": "Baseline thesis is available with enough context for the report.",
      "bull_case": "Bull case is available with enough context for the report.",
      "bear_case": "Bear case is available with enough context for the report.",
      "judge_verdict": {
          "valid": True,
          "target_price_myr": 0.30,
          "raw_judgement": "Recommendation: SELL\nTarget Price: RM 0.30\nConfidence: Medium",
      },
      "node_metrics": [],
  }

  report = report_agent_node(state)["final_report"]

  assert "| Net income | 0.06 |" in report


def test_report_agent_parses_mojibake_stop_loss_and_judge_target():
  state = {
      "ticker": "0157.KL",
      "company_name": "Focus Point Holdings Berhad",
      "workflow_status": "SUCCESS",
      "raw_data": {
          "fundamentals": {
              "symbol": "0157.KL",
              "source": "yfinance",
              "status": "SUCCESS",
              "company_name": "Focus Point Holdings Berhad",
              "sector": "Healthcare",
              "industry": "Medical Instruments & Supplies",
              "current_price": 0.505,
              "pe_ratio": 8.42,
              "forward_pe": 7.23,
              "dividend_yield": 5.94,
              "fifty_two_week_low": 0.455,
              "fifty_two_week_high": 0.565,
          },
          "quarterly_reports": {"source": "yfinance", "status": "SUCCESS"},
      },
      "valuation_model": {
          "status": "SUCCESS",
          "estimated_fair_value_myr": 0.84,
          "pe_ratio_used": 8.42,
      },
      "baseline_thesis": "Lowâ€‘multiple profile.",
      "bull_case": "Bullâ€‘case text.",
      "bear_case": "Bearâ€‘case text.",
      "judge_verdict": {
          "valid": True,
          "target_price_myr": 0.84,
          "raw_judgement": (
              "Recommendation: HOLD\n"
              "Entry Price: MYR 0.505\n"
              "Target Price: MYR 0.525\n"
              "Stopâ€‘Loss: MYR 0.475\n"
              "Confidence: Medium\n"
              "Rationale: modest upside."
          ),
      },
      "node_metrics": [],
  }

  report = report_agent_node(state)["final_report"]

  assert "| HOLD | RM 0.53 | RM 0.505" in report
  assert "**Stop-Loss:** MYR 0.475" in report
  assert "DCF fair value reference: RM 0.84" in report
  assert "â€" not in report
  assert "Ã" not in report


def test_compact_raw_data_limits_large_provider_payloads():
  raw = {
      "fundamentals": {
          "symbol": "5275.KL",
          "company_name": "MYNEWS",
          "summary": "x" * 2000,
          "current_price": 0.78,
          "market_cap": 123,
          "irrelevant_blob": "y" * 5000,
      },
      "quarterly_reports": {
          "source": "tavily_search",
          "status": "FALLBACK_SUCCESS",
          "extracted_reports": [
              {"title": "A", "content": "z" * 2000, "url": "https://example.test/a"},
              {"title": "B", "content": "z" * 2000, "url": "https://example.test/b"},
              {"title": "C", "content": "z" * 2000, "url": "https://example.test/c"},
          ],
      },
  }

  compact = _compact_raw_data(raw)

  assert "irrelevant_blob" not in compact["fundamentals"]
  assert len(compact["fundamentals"]["summary"]) <= 500
  assert len(compact["quarterly_reports"]["extracted_reports"]) == 2
  assert len(compact["quarterly_reports"]["extracted_reports"][0]["content"]) <= 250


def test_compact_raw_data_summarizes_quarterly_financials_for_llm_prompts():
  raw = {
      "fundamentals": {"symbol": "4677.KL", "current_price": 2.12},
      "quarterly_reports": {
          "source": "yfinance",
          "status": "SUCCESS",
          "quarterly_financials": {
              "2026-03-31T00:00:00": {
                  "Total Revenue": 7_568_828_000,
                  "EBITDA": 2_132_488_000,
                  "Net Income": 325_994_000,
                  "Diluted EPS": 0.015,
                  "Very Large Unneeded Row": "x" * 5000,
              }
          },
      },
  }

  compact = _compact_raw_data(raw)
  quarterly = compact["quarterly_reports"]

  assert "quarterly_financials" not in quarterly
  assert quarterly["quarterly_summary"][0]["revenue"] == "7568.8"
  assert quarterly["quarterly_summary"][0]["ebitda"] == "2132.5"
  assert quarterly["quarterly_summary"][0]["eps"] == "0.0150"
  assert "Very Large Unneeded Row" not in str(quarterly)


def test_compact_raw_data_labels_qoq_and_yoy_revenue_comparisons():
  raw = {
      "fundamentals": {"symbol": "5275.KL", "current_price": 0.43},
      "quarterly_reports": {
          "source": "yfinance",
          "status": "SUCCESS",
          "quarterly_financials": {
              "2026-04-30T00:00:00": {"Total Revenue": 77_300_000, "Diluted EPS": 0.015},
              "2026-01-31T00:00:00": {"Total Revenue": 91_200_000, "Diluted EPS": 0.018},
              "2025-10-31T00:00:00": {"Total Revenue": 80_000_000},
              "2025-07-31T00:00:00": {"Total Revenue": 78_000_000},
              "2025-04-30T00:00:00": {"Total Revenue": 70_000_000},
          },
      },
  }

  compact = _compact_raw_data(raw)
  latest = compact["quarterly_reports"]["quarterly_summary"][0]

  assert latest["eps"] == "0.0150"
  assert latest["qoq_revenue_change_pct"] == "-15.2%"
  assert latest["yoy_revenue_change_pct"] == "10.4%"
  assert "QoQ compares adjacent quarters" in latest["comparison_note"]


def test_synthesis_agent_does_not_crash_when_quarterly_revenue_summary_is_present(monkeypatch):
  class FailingLLM:
    model = "failing-test"

    def invoke(self, prompt):
      raise ConnectionError("LLM unavailable")

  monkeypatch.setattr("nodes.heavy_llm", FailingLLM())
  monkeypatch.setattr("nodes.fast_llm", FailingLLM())
  state = {
      "ticker": "0157.KL",
      "raw_data": {
          "fundamentals": {"symbol": "0157.KL", "current_price": 0.505},
          "quarterly_reports": {
              "source": "yfinance",
              "status": "SUCCESS",
              "quarterly_financials": {
                  "2026-03-31T00:00:00": {"Total Revenue": 77_300_000, "Diluted EPS": 0.015},
                  "2025-12-31T00:00:00": {"Total Revenue": 91_200_000, "Diluted EPS": 0.018},
              },
          },
      },
      "research_plan": {"data_quality": "partial"},
      "financial_metrics": {"analysis_notes": "ok"},
      "valuation_model": {
          "estimated_fair_value_myr": 0.84,
          "upside_downside_pct": 66.27,
          "wacc": 0.08,
          "growth_rate": 0.08,
          "terminal_pe": 14.0,
      },
  }

  output = synthesis_agent_node(state)

  assert "workflow_status" not in output
  assert "0.0150" in output["baseline_thesis"]
  assert "FALLBACK [synthesis_agent]" in output["audit_trace"][0]
  assert output["node_metrics"][0]["status"] == "fallback"
