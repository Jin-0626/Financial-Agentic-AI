import argparse
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable, Optional

from langsmith import Client

from settings import settings


def _seconds(run: Any) -> Optional[float]:
    start = getattr(run, "start_time", None)
    end = getattr(run, "end_time", None)
    if not start or not end:
        return None
    return max((end - start).total_seconds(), 0.0)


def _tokens(run: Any) -> tuple[Optional[int], Optional[int], Optional[int]]:
    prompt = getattr(run, "prompt_tokens", None)
    completion = getattr(run, "completion_tokens", None)
    total = getattr(run, "total_tokens", None)
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    return prompt, completion, total


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    return statistics.mean(clean) if clean else None


def _percentile(values: list[float], percentile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = min(int(round((len(ordered) - 1) * percentile)), len(ordered) - 1)
    return ordered[index]


def _fmt_seconds(value: Optional[float]) -> str:
    return "unknown" if value is None else f"{value:.1f}s"


def _fmt_int(value: Optional[float]) -> str:
    return "unknown" if value is None else f"{int(round(value)):,}"


def _ticker(run: Any) -> str:
    metadata = getattr(run, "extra", {}) or {}
    if isinstance(metadata, dict):
        meta = metadata.get("metadata", metadata)
        if isinstance(meta, dict):
            return meta.get("ticker") or meta.get("company_name") or getattr(run, "name", "N/A")
    return getattr(run, "name", "N/A")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit recent LangSmith Financial Analyst traces.")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--ticker", default=None)
    args = parser.parse_args()

    print("LANGSMITH EXECUTION AUDIT")
    print(f"Project: {settings.LANGSMITH_PROJECT}\n")
    try:
        client = Client()
        runs = list(
            client.list_runs(
                project_name=settings.LANGSMITH_PROJECT,
                execution_order=1,
                limit=args.limit,
            )
        )
    except Exception as exc:
        print(f"Unable to query LangSmith: {type(exc).__name__}: {exc}")
        return 1

    if args.ticker:
        runs = [run for run in runs if args.ticker.upper() in _ticker(run).upper() or args.ticker.upper() in getattr(run, "name", "").upper()]

    if not runs:
        print("No runs found for this project/filter.")
        return 0

    runtimes = [_seconds(run) for run in runs]
    complete_runtimes = [runtime for runtime in runtimes if runtime is not None]
    prompt_tokens = []
    completion_tokens = []
    total_tokens = []
    for run in runs:
        prompt, completion, total = _tokens(run)
        prompt_tokens.append(prompt)
        completion_tokens.append(completion)
        total_tokens.append(total)

    print(f"Runs analysed: {len(runs)}\n")
    print("Overall:")
    print(f"Mean runtime: {_fmt_seconds(_mean(runtimes))}")
    print(f"P50 runtime: {_fmt_seconds(_percentile(complete_runtimes, 0.50))}")
    print(f"P95 runtime: {_fmt_seconds(_percentile(complete_runtimes, 0.95))}\n")
    print(f"Average prompt tokens: {_fmt_int(_mean(prompt_tokens))}")
    print(f"Average completion tokens: {_fmt_int(_mean(completion_tokens))}")
    print(f"Average total tokens: {_fmt_int(_mean(total_tokens))}\n")

    slowest = max(runs, key=lambda run: _seconds(run) or -1)
    highest = max(runs, key=lambda run: _tokens(run)[2] or -1)
    print(f"Slowest run: {_ticker(slowest)} / {_fmt_seconds(_seconds(slowest))}")
    print(f"Highest-token run: {_ticker(highest)} / {_fmt_int(_tokens(highest)[2])} tokens\n")

    child_stats: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"runtime": [], "tokens": []})
    for parent in runs:
        try:
            children = list(client.list_runs(project_name=settings.LANGSMITH_PROJECT, trace_id=parent.trace_id))
        except Exception:
            children = []
        for child in children:
            if getattr(child, "id", None) == getattr(parent, "id", None):
                continue
            name = getattr(child, "name", "unknown")
            runtime = _seconds(child)
            total = _tokens(child)[2]
            if runtime is not None:
                child_stats[name]["runtime"].append(runtime)
            if total is not None:
                child_stats[name]["tokens"].append(float(total))

    if child_stats:
        print(f"{'Agent':<24} {'Avg Runtime':>12} {'Avg Tokens':>12}")
        for name, values in sorted(child_stats.items()):
            avg_runtime = _mean(values["runtime"])
            avg_tokens = _mean(values["tokens"])
            print(f"{name:<24} {_fmt_seconds(avg_runtime):>12} {_fmt_int(avg_tokens):>12}")
    else:
        print("No child-run statistics available for these traces.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
