from langgraph.graph import StateGraph, END
from langsmith import traceable
import time
from state import BursaAgentState
from nodes import (
    data_agent_node,
    planner_agent_node,
    analysis_agent_node,
    modeling_agent_node,
    synthesis_agent_node,
    bull_agent_node,
    bear_agent_node,
    debate_agent_node,
    replanner_agent_node,
    judge_agent_node,
    report_agent_node
)
from settings import settings
from observability import summarize_telemetry


def create_bursa_agent_graph():
    workflow = StateGraph(BursaAgentState)

    workflow.add_node("data_agent", data_agent_node)
    workflow.add_node("planner_agent", planner_agent_node)
    workflow.add_node("analysis_agent", analysis_agent_node)
    workflow.add_node("modeling_agent", modeling_agent_node)
    workflow.add_node("synthesis_agent", synthesis_agent_node)
    workflow.add_node("bull_agent", bull_agent_node)
    workflow.add_node("bear_agent", bear_agent_node)
    workflow.add_node("debate_agent", debate_agent_node)
    workflow.add_node("replanner_agent", replanner_agent_node)
    workflow.add_node("judge_agent", judge_agent_node)
    workflow.add_node("report_agent", report_agent_node)

    workflow.set_entry_point("planner_agent")
    workflow.add_edge("planner_agent", "data_agent")
    workflow.add_edge("data_agent", "analysis_agent")
    workflow.add_edge("data_agent", "modeling_agent")
    workflow.add_edge(["analysis_agent", "modeling_agent"], "synthesis_agent")


    workflow.add_edge("synthesis_agent", "bull_agent")
    workflow.add_edge("synthesis_agent", "bear_agent")

    workflow.add_edge(["bull_agent", "bear_agent"], "debate_agent")
    workflow.add_edge("debate_agent", "judge_agent")
    workflow.add_edge("judge_agent", "replanner_agent")
    workflow.add_edge("replanner_agent", "report_agent")
    workflow.add_edge("report_agent", END)

    return workflow.compile()


def langsmith_config(ticker: str, *, period: str = "n/a", company_name: str | None = None) -> dict:
    return {
        "run_name": f"Financial Analysis: {ticker}",
        "project_name": settings.LANGSMITH_PROJECT,
        "tags": ["financial-analysis", "bursa-malaysia", ticker, period],
        "metadata": {
            "ticker": ticker,
            "company_name": company_name,
            "primary_model": settings.PRIMARY_MODEL,
            "fast_model": getattr(settings, "FAST_MODEL", "minimax-m3:cloud"),
        },
    }


@traceable(name="Financial Analysis", run_type="chain")
def run_financial_analysis(ticker: str, *, period: str = "n/a", company_name: str | None = None) -> BursaAgentState:
    graph = create_bursa_agent_graph()
    initial_state = {
        "ticker": ticker,
        "debate_rounds": 0,
        "audit_trace": [],
        "node_metrics": [],
        "errors": [],
    }
    start = time.perf_counter()
    final_state = graph.invoke(initial_state, config=langsmith_config(ticker, period=period, company_name=company_name))
    metrics = list(final_state.get("node_metrics", []))
    summary = summarize_telemetry(metrics)
    summary["runtime_ms"] = round((time.perf_counter() - start) * 1000, 2)
    final_state["workflow_metrics"] = summary
    return final_state
