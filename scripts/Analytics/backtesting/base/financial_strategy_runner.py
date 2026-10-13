"""Financial Deep Agents' independently implemented offline strategy adapter."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from financial_analytics.schemas import Backtest
from financial_analytics.calculations import backtest

from financial_analytics.registry import strategy_catalog

STRATEGIES=tuple(item["id"] for item in strategy_catalog())

class FinancialStrategyRunner:
    def list_strategies(self):
        return strategy_catalog()

    def get_strategy_info(self,strategy_id):
        return next((item for item in self.list_strategies() if item["id"]==strategy_id),None)

    def execute_strategy(self,strategy_id,params):
        try:
            if strategy_id not in STRATEGIES: raise ValueError("Strategy unavailable; select a built-in offline strategy")
            if "strategy" in params and params["strategy"]!=strategy_id: raise ValueError("Conflicting strategy identifiers")
            result=backtest(Backtest.model_validate({**params,"strategy":strategy_id}))
            return {"success":True,"data":result}
        except (ValueError,ArithmeticError) as error:
            return {"success":False,"error":str(error)[:400]}

if __name__=="__main__":
    runner=FinancialStrategyRunner()
    result=runner.list_strategies() if len(sys.argv)==2 and sys.argv[1]=="list" else runner.execute_strategy(sys.argv[2],json.loads(sys.argv[3])) if len(sys.argv)==4 and sys.argv[1]=="run" else {"success":False,"error":"Use list or run STRATEGY JSON"}
    print(json.dumps(result,allow_nan=False))
