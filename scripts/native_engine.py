"""Local administrative ingestion; never exposed as an agent tool or API route."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from orchestrator.tools.native_snapshots import fetch_yahoo_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Import normalized snapshots into the local native engine")
    parser.add_argument("mode", choices=["yahoo-prices", "yahoo-statements", "import"])
    parser.add_argument("--ticker")
    parser.add_argument("--file", type=Path)
    parser.add_argument("--lookback-days", type=int, default=252)
    args = parser.parse_args()
    org_id = os.environ.get("ENGINE_ORG_ID")
    if not org_id:
        parser.error("ENGINE_ORG_ID is required")
    command = json.loads(os.environ.get("ENGINE_COMMAND", '["target/debug/financial-engine"]'))
    if not isinstance(command, list) or not command or not all(isinstance(v,str) for v in command):
        parser.error("ENGINE_COMMAND must be a JSON argument list")
    if args.mode == "import":
        if args.file is None or args.file.stat().st_size > 8 * 1024 * 1024:
            parser.error("A bounded normalized snapshot file is required")
        body = args.file.read_bytes()
    else:
        if not args.ticker:
            parser.error("--ticker is required")
        snapshot = fetch_yahoo_snapshot("prices" if args.mode == "yahoo-prices" else "statements",
                                      org_id=org_id, ticker=args.ticker, lookback_days=args.lookback_days)
        body = json.dumps(snapshot,allow_nan=False).encode()
    outcome = subprocess.run(command+["import"], input=body, capture_output=True, timeout=60)
    if outcome.returncode:
        print("Native snapshot import failed",file=sys.stderr)
        return 1
    result = json.loads(outcome.stdout)
    print(json.dumps({"snapshot_id":result["snapshot_id"]}))
    return 0

if __name__ == "__main__":
    try: raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError):
        print("Native ingestion unavailable",file=sys.stderr)
        raise SystemExit(1)
