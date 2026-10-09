"""Independent aggregation: keep unsuccessful attempts and unavailable measurements."""
import math
import statistics

def aggregate(runs):
    durations = sorted(r["duration_s"] for r in runs)
    requests = sum(r["tool_requests"] for r in runs)
    failures = sum(r["tool_failures"] for r in runs)
    unresolved = sum(max(0, r["tool_requests"] - r["tool_results"]) for r in runs)
    return {"runs": len(runs), "completed_reports": sum(bool(r["success"]) for r in runs),
        "median_duration_s": statistics.median(durations) if durations else None,
        "p95_duration_s": durations[math.ceil(.95 * len(durations))-1] if durations else None,
        "maximum_duration_s": max(durations) if durations else None,
        "reported_tokens": sum(r["tokens_used"] or 0 for r in runs),
        "missing_token_runs": sum(r["tokens_used"] is None for r in runs),
        "tool_failure_numerator": failures, "tool_request_denominator": requests,
        "tool_failure_rate": failures / requests if requests else None,
        "unresolved_tool_requests": unresolved,
        "cost_usd": None, "input_output_token_split": None,
        "cancellation_latency_s": None}
