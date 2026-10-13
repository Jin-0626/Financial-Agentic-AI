"""Summarize recorded live outcomes without treating successful output as accurate."""
import json, sys, re
from pathlib import Path
from evaluation.metrics import aggregate

def main():
    root = Path(sys.argv[1])
    data = json.loads((root / "results.json").read_text())
    summary = aggregate(data["runs"])
    rows = []
    for run in data["runs"]:
        artifact = json.loads((root / f"{run['repeat']}-{run['case']}.json").read_text())
        starts = {event["id"]: event["elapsed_s"] for event in artifact["events"] if event.get("type") == "tool_call"}
        durations = [{"tool": event.get("name"), "elapsed_s": event["elapsed_s"]-starts[event["id"]], "status": event.get("status")} for event in artifact["events"] if event.get("type") == "tool_result" and event.get("id") in starts]
        report = (root/f"{run['repeat']}-{run['case']}.md").read_text(encoding="utf-8")
        rows.append({**run, "activity_durations": durations, "internal_evidence_ids_visible": bool(re.search(r"ev_[0-9a-f]{8,}", report))})
    summary["all_required_sections_present"] = sum(all(r["required_sections"].values()) for r in rows)
    summary["reports_with_internal_ids"] = sum(r["internal_evidence_ids_visible"] for r in rows)
    summary["latency_note"] = "Tool durations are visible activity intervals including queueing, not provider-only network latency"
    summary["factual_quality"] = "See independent quality-review.json; completion does not imply accuracy"
    (root/"summary.json").write_text(json.dumps({"aggregate": summary, "runs": rows},indent=2),encoding="utf-8")
    lines=["# Live evaluation results", "", "| Case | Repeat | Report | Seconds | Tokens | Tool failures / requests |", "| --- | --- | --- | ---: | ---: | ---: |"]
    lines += [f"| {r['case']} | {r['repeat']} | {r['success']} | {r['duration_s']} | {r['tokens_used']} | {r['tool_failures']} / {r['tool_requests']} |" for r in rows]
    lines += ["", "Aggregate:", "", "```json", json.dumps(summary,indent=2), "```", "", "This campaign is not an SLA. Dollar costs, input/output splits and cancellation latency remain unavailable. Independently verified critical report errors prevent release acceptance."]
    (root/"summary.md").write_text("\n".join(lines),encoding="utf-8")
    print(json.dumps(summary,indent=2))
if __name__ == "__main__":main()
