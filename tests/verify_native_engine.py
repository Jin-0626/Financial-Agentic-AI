"""Opt-in real-engine OTLP verification, entirely synthetic and local."""
import json
import os
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ["OTEL_ENABLED"]="true"
os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT","http://127.0.0.1:14317")
import requests
from opentelemetry import trace
from orchestrator.telemetry import initialize_telemetry, operation, inject_mcp_context
from test_native_engine import NativeEngineTests, Session


def main():
    NativeEngineTests.setUpClass()
    telemetry=initialize_telemetry()
    session=Session(NativeEngineTests.command("serve"),NativeEngineTests.env)
    try:
        session.request(1,"initialize",{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"local-trace-check","version":"1"}})
        session.write({"jsonrpc":"2.0","method":"notifications/initialized"})
        with operation("research.request"):
            with operation("mcp.tools.call",kind=trace.SpanKind.CLIENT) as client:
                trace_id=format(client.get_span_context().trace_id,"032x")
                parent_id=format(client.get_span_context().span_id,"016x")
                response=session.request(2,"tools/call",inject_mcp_context({"name":"calculate_historical_var","arguments":{"ticker":"0157.KL","lookback_days":4,"confidence_level":0.95}}))
                assert not response["result"]["isError"],response
        session.close(); session=None
        telemetry.traces.force_flush(1000); telemetry.metrics.force_flush(1000)
        deadline=time.monotonic()+30
        collected=None
        while time.monotonic()<deadline:
            data=requests.get("http://127.0.0.1:16686/api/traces/"+trace_id,timeout=2).json().get("data") or []
            if data and {"research.request","mcp.tools.call","mcp.tools.handle","quant.compute","data.fetch","result.deserialize"}.issubset({s["operationName"] for s in data[0]["spans"]}):
                collected=data[0]; break
            time.sleep(0.5)
        assert collected is not None,"Native spans did not reach Jaeger"
        server=next(s for s in collected["spans"] if s["operationName"]=="mcp.tools.handle")
        worker=next(s for s in collected["spans"] if s["operationName"]=="quant.compute")
        assert any(ref["spanID"]==parent_id for ref in server["references"]),"Python-to-supervisor parentage failed"
        assert any(ref["spanID"]==server["spanID"] for ref in worker["references"]),"Supervisor-to-worker parentage failed"
        encoded=json.dumps(collected)
        assert "native-test" not in encoded and "reviewed fixture" not in encoded,"Snapshot identity or source entered traces"
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            metrics=requests.get("http://127.0.0.1:18889/metrics",timeout=2).text
            if all(name in metrics for name in ["mcp_queue_depth","mcp_write_wait","tool_active","tool_duration"]): break
            time.sleep(0.5)
        else: raise AssertionError("Native metrics did not reach Collector")
        print(json.dumps({"trace_id":trace_id,"spans":len(collected["spans"]),"native_parentage":"verified","metrics":"verified","privacy":"verified"}))
    finally:
        if session: session.close()
        telemetry.release()
        NativeEngineTests.tearDownClass()

if __name__=="__main__": main()
