"""Financial Deep Agents offline backtesting command-line compatibility adapter."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[4]))
from scripts.Analytics.backtesting.base.financial_strategy_runner import FinancialStrategyRunner

class FinancialProvider:
    def __init__(self): self.runner=FinancialStrategyRunner()
    def get_strategies(self,request): return {"success":True,"data":{"provider":"financial","strategies":{"Offline":self.runner.list_strategies()}}}
    def get_indicators(self,request): return {"indicators":{}}
    def run_backtest(self,request):
        params=dict(request);strategy=params.pop("strategy_id",params.pop("strategy","buy_and_hold"))
        return self.runner.execute_strategy(strategy,params)

if __name__=="__main__":
    provider=FinancialProvider()
    methods={"get_strategies":provider.get_strategies,"get_indicators":provider.get_indicators,"run_backtest":provider.run_backtest,"execute_financial_strategy":provider.run_backtest}
    command=sys.argv[1] if len(sys.argv)>1 else "get_strategies"
    result=methods[command](json.loads(sys.argv[2]) if len(sys.argv)>2 else {}) if command in methods else {"success":False,"error":"Unsupported offline command"}
    print(json.dumps(result,allow_nan=False))
