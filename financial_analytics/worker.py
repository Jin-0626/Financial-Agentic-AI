"""Single-job offline worker with a strict bounded JSON protocol."""
import contextlib
import json
import socket
import sys
from pydantic import ValidationError
from financial_analytics.registry import validate
from financial_analytics.calculations import calculate

MAX_FRAME = 1048576


def offline(*args, **kwargs):
    raise RuntimeError("Network access is disabled in offline analytics")


def unique(pairs):
    result = {}
    for key,value in pairs:
        if key in result: raise ValueError("Duplicate JSON key")
        result[key]=value
    return result


def main():
    socket.create_connection = offline
    socket.socket.connect = offline
    socket.socket.connect_ex = offline
    socket.getaddrinfo = offline
    try:
        raw = sys.stdin.buffer.readline(MAX_FRAME+1)
        if len(raw)>MAX_FRAME or not raw.endswith(b"\n"): raise ValueError("Analytics request exceeds frame limit")
        job=json.loads(raw,object_pairs_hook=unique,parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
        if set(job)!={"operation","inputs"}: raise ValueError("Invalid analytics request")
        args=validate(job["operation"],job["inputs"])
        from financial_analytics.registry import OPERATIONS
        if OPERATIONS[job["operation"]].native_tool:
            raise ValueError("Operation requires the Rust engine")
        with contextlib.redirect_stdout(sys.stderr): result=calculate(job["operation"],args)
        response={"status":"success","operation":job["operation"],"result":result,"provenance":{"engine":"isolated_python","method_version":"financial-analytics-v1","data_source":"caller-supplied observations","offline":True,"currency":getattr(args,"currency",None),"limitations":["Monetary units are unspecified when currency is absent; do not infer an exchange rate"]}}
        encoded=json.dumps(response,allow_nan=False,separators=(",",":"))
        if len(encoded.encode())>=MAX_FRAME: raise ValueError("Analytics result exceeds frame limit; request a smaller workload")
    except (ValueError,ArithmeticError,TypeError,ImportError,RuntimeError) as error:
        # Inputs and provider credentials must not appear in worker diagnostics.
        encoded=json.dumps({"status":"error","category":"dependency" if isinstance(error,ImportError) else "calculation","error":"Invalid analytics inputs" if isinstance(error,ValidationError) else str(error)[:400]})
    sys.stdout.write(encoded+"\n")


if __name__=="__main__": main()
