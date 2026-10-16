"""In-process baseline timing inside the isolated analytics interpreter."""
import contextlib, json, statistics, sys, time
from financial_analytics.registry import validate
from evaluation.baselines.native import calculate

def main():
    raw=sys.stdin.buffer.readline(1048577)
    if len(raw)>1048576: raise ValueError("Frame exceeded")
    job=json.loads(raw);args=validate(job["operation"],job["inputs"])
    times=[]
    with contextlib.redirect_stdout(sys.stderr):
        for _ in range(7):
            start=time.perf_counter();result=calculate(job["operation"],args);times.append((time.perf_counter()-start)*1000)
    print(json.dumps({"result":result,"calculation_ms":statistics.median(times)},allow_nan=False))
if __name__=="__main__":main()
