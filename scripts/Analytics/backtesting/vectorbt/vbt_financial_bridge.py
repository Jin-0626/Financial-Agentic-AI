"""Compatibility bridge to the independently implemented offline strategies."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[4]))
from scripts.Analytics.backtesting.base.financial_strategy_runner import FinancialStrategyRunner


def run_financial_strategy(strategy_id,params_dict):
    return FinancialStrategyRunner().execute_strategy(strategy_id,params_dict)

if __name__=="__main__":
    result=run_financial_strategy(sys.argv[1],json.loads(sys.argv[2])) if len(sys.argv)==3 else {"success":False,"error":"Supply a strategy and JSON inputs"}
    print(json.dumps(result,allow_nan=False))
