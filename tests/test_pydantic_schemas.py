import pytest
from pydantic import ValidationError

from observability import extract_token_usage, summarize_telemetry
from schemas import BursaTickerRequest, FailureState, NodeMetric, QuarterlyReportResult, ResearchPlan, StockInfoResult
from tools import calculate_dcf_val


@pytest.mark.unit
def test_ticker_request_rejects_empty_or_symbol_only_inputs():
    with pytest.raises(ValidationError):
        BursaTickerRequest(query="")

    with pytest.raises(ValidationError):
        BursaTickerRequest(query="@@@@")


@pytest.mark.unit
def test_stock_info_schema_normalizes_invalid_numeric_values():
    result = StockInfoResult(
        symbol="5275.KL",
        company_name="MYNEWS",
        source="yfinance",
        status="SUCCESS",
        current_price=float("nan"),
        pe_ratio=-10,
    )

    assert result.current_price is None
    assert result.pe_ratio is None


@pytest.mark.unit
def test_quarterly_schema_marks_tavily_as_fallback():
    result = QuarterlyReportResult(
        symbol="5275.KL",
        company_name="MYNEWS",
        source="tavily_search",
        status="FALLBACK_SUCCESS",
    )

    assert result.fallback_used is True
    assert result.fallback_provider == "tavily_search"


@pytest.mark.unit
def test_dcf_refuses_missing_price_instead_of_defaulting_to_one_ringgit():
    result = calculate_dcf_val.invoke({"current_price": None, "pe_ratio": 10})

    assert result["status"] == "FAILED"
    assert "current price is unavailable" in result["error"]
    assert result["estimated_fair_value_myr"] is None


@pytest.mark.unit
@pytest.mark.parametrize("pe_ratio", [None, 0, -5, float("nan")])
def test_dcf_documents_pe_substitution(pe_ratio):
    result = calculate_dcf_val.invoke({"current_price": 1.0, "pe_ratio": pe_ratio})

    assert result["status"] == "SUCCESS"
    assert result["pe_ratio_used"] == 15.0
    assert result["pe_ratio_substituted"] is True
    assert result["pe_ratio_substitution_reason"] == "FBM KLCI market baseline"


@pytest.mark.unit
def test_dcf_known_value_with_positive_pe():
    current_price = 1.0
    pe_ratio = 10
    growth_rate = 0.08
    eps = current_price / pe_ratio
    fair_value = (eps * ((1 + growth_rate) ** 5) * 14.0) / ((1 + 0.08) ** 5)

    result = calculate_dcf_val.invoke(
        {"current_price": current_price, "pe_ratio": pe_ratio, "growth_rate": growth_rate}
    )

    assert result["status"] == "SUCCESS"
    assert result["pe_ratio_substituted"] is False
    assert result["estimated_fair_value_myr"] == round(fair_value, 2)
    assert result["growth_rate"] == growth_rate
    assert result["wacc"] == 0.08
    assert result["discount_rate"] == 0.08
    assert result["terminal_growth_rate"] == 0.02
    assert result["terminal_pe"] == 14.0
    assert result["eps_input"] == round(eps, 4)
    assert len(result["projected_eps"]) == 5
    assert len(result["projected_fcff_per_share"]) == 5


@pytest.mark.unit
def test_node_metric_computes_total_tokens_when_available():
    metric = NodeMetric(
        node="analysis_agent",
        latency_ms=1.2,
        prompt_tokens=10,
        completion_tokens=5,
        status="success",
    )

    assert metric.total_tokens == 15


@pytest.mark.unit
def test_missing_llm_token_metadata_stays_unknown():
    class Response:
        content = "ok"

    usage = extract_token_usage(Response())

    assert usage == {
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
    }


@pytest.mark.unit
def test_summarize_telemetry_reports_evaluation_metrics():
    summary = summarize_telemetry(
        [
            {
                "node": "data_agent",
                "latency_ms": 10,
                "status": "success",
                "tool_calls": 2,
                "tool_errors": 1,
                "llm_calls": 0,
                "retry_count": 0,
            },
            {
                "node": "judge_agent",
                "latency_ms": 40,
                "status": "fallback",
                "tool_calls": 0,
                "tool_errors": 0,
                "llm_calls": 1,
                "retry_count": 2,
                "downgraded": True,
                "fallback_used": True,
            },
        ]
    )

    assert summary["task_success_rate"] == 1.0
    assert summary["tool_call_error_rate"] == 0.5
    assert summary["p99_latency_ms"] == 40
    assert summary["retry_count"] == 2
    assert summary["downgraded_count"] == 1


@pytest.mark.unit
def test_failure_state_defaults_to_failed_stage_audit_trace():
    failure = FailureState(
        failed_stage="synthesis_agent",
        error_message="synthesis_agent returned an empty LLM response.",
    )

    assert failure.workflow_status == "FAILED"
    assert failure.errors[0].node == "synthesis_agent"
    assert failure.audit_trace == [
        "FAILED [synthesis_agent]: synthesis_agent returned an empty LLM response."
    ]


@pytest.mark.unit
def test_research_plan_rejects_unknown_agents():
    with pytest.raises(ValidationError):
        ResearchPlan(
            ticker="0157.KL",
            company_name="Focus Point Holdings Berhad",
            research_objective="Create a company update.",
            required_agents=["analysis_agent", "unknown_agent"],
            data_quality="complete",
            valuation_method="dcf_pe_proxy",
        )
