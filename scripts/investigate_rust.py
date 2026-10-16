"""Priority 3 investigation: numerical prototypes and normalization boundaries."""
import json, math, os, platform, statistics, subprocess, sys, time
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.infrastructure.offline_worker import worker_python
from financial_analytics.registry import validate
from evaluation.offline_workloads import compare
from app.infrastructure.providers import price_series
import pandas as pd

def main():
    suffix=".exe" if os.name=="nt" else ""
    executable=ROOT/"target/release/examples"/("statistics_probe"+suffix)
    report={"platform":platform.platform(),"repetitions":7,"production_migration":False,"statistics":[],"normalization":[]}
    for operation in ("descriptive_statistics","covariance_matrix","autocorrelation"):
        for count in (30,1000,10000):
            data={"values":[math.sin(i/13)+.0001*i for i in range(count)]}
            if operation=="covariance_matrix":data={"values":[[math.sin(i/(13+j))+.0001*i for j in range(5)] for i in range(count)]}
            inputs=validate(operation,data).model_dump(mode="json");job=json.dumps({"operation":operation,"inputs":inputs});row={"operation":operation,"observations":count,"frame_bytes":len(job.encode())}
            if len(job.encode())>1048576:row["status"]="exceeds_existing_frame_limit"
            else:
                baseline=json.loads(subprocess.check_output([str(worker_python()),"-m","evaluation.baselines.timing"],input=job,text=True))
                native=json.loads(subprocess.check_output([str(executable)],input=job,text=True))
                compare(native["result"],baseline["result"])
                row.update(parity=True,python_calculation_ms=baseline["calculation_ms"],rust_calculation_ms=native["calculation_ms"])
            report["statistics"].append(row);print(json.dumps(row),flush=True)
    for count in (252,2520):
        for assets in (10,50,100):
            symbols=[f"S{i}" for i in range(assets)]
            frame=pd.DataFrame({(symbol,"Close"):[100+.01*i for i in range(count)] for symbol in symbols},index=pd.date_range("2015-01-01",periods=count))
            with patch("app.infrastructure.providers.yf.download",return_value=frame):
                times=[]
                for _ in range(7):
                    start=time.perf_counter();data=price_series(symbols,{s:"USD" for s in symbols},"5y");times.append((time.perf_counter()-start)*1000)
            start=time.perf_counter();encoded=json.dumps(data);serialization_ms=(time.perf_counter()-start)*1000
            row={"observations":count,"symbols":assets,"normalization_ms":statistics.median(times),"serialization_ms":serialization_ms,"output_bytes":len(encoded.encode()),"fits_mcp_frame":len(encoded.encode())<1048576,"provider_network_ms":0,"supported_portfolio_size":assets<=50}
            report["normalization"].append(row);print(json.dumps(row),flush=True)
    (ROOT/"evaluation/rust_investigation.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
if __name__=="__main__":main()
