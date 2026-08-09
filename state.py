from typing import Annotated, Any, Dict, List, Optional, TypedDict
import operator

class BursaAgentState(TypedDict):
    ticker: str
    company_name: str
    raw_data: Dict[str, Any]
    research_plan: Dict[str, Any]
    replan_decision: Dict[str, Any]
    financial_metrics: Dict
    valuation_model: Dict
    baseline_thesis: str
    bull_case: str
    bear_case: str
    debate_brief: Dict[str, Any]
    debate_rounds: int
    judge_verdict: Dict
    final_report: str
    workflow_status: str
    failed_stage: Optional[str]
    error_message: Optional[str]
    errors: Annotated[List[Dict[str, Any]], operator.add]
    audit_trace: Annotated[List[str], operator.add]
    node_metrics: Annotated[List[Dict[str, Any]], operator.add]
    workflow_metrics: Dict[str, Any]
