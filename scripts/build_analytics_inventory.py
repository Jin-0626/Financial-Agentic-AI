"""Build a deterministic coverage inventory without importing reference modules."""
import ast
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CATEGORIES={"alternateInvestment":"alternatives","corporateFinance":"corporate_finance","derivatives":"derivatives","options":"derivatives","economics":"economics","equityInvestment":"valuation","finanicalanalysis":"financial_statements","fixedIncome":"fixed_income","portfolioManagement":"portfolio","technical_analysis":"technical","backtesting":"backtesting","quant":"statistics","python_skfolio_lib":"optimization","pyportfolioopt_wrapper":"optimization","fortitudo_tech_wrapper":"optimization","py_vollib_wrapper":"derivatives","pypme_wrapper":"alternatives","vnpy_wrapper":"backtesting","gs_quant_wrapper":"statistics","ffn_wrapper":"portfolio","statsmodels_wrapper":"statistics","functime_wrapper":"forecasting","gluonts_wrapper":"forecasting","pmdarima_wrapper":"forecasting","tsmoothie_wrapper":"forecasting"}


def build():
    from financial_analytics.registry import OPERATIONS
    records=[]
    for path in sorted((ROOT/"scripts"/"Analytics").rglob("*.py")):
        relative=path.relative_to(ROOT).as_posix();tree=ast.parse(path.read_text(encoding="utf-8-sig"));folder=path.relative_to(ROOT/"scripts"/"Analytics").parts[0]
        category=CATEGORIES.get(folder,"statistics")
        entries=[]
        for node in tree.body:
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and not node.name.startswith("_"): entries.append(node.name)
            elif isinstance(node,ast.ClassDef): entries.extend(node.name+"."+method.name for method in node.body if isinstance(method,(ast.FunctionDef,ast.AsyncFunctionDef)) and not method.name.startswith("_"))
        dependencies=sorted({node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node,ast.ImportFrom) and node.module})
        replaced="financial_strategy_runner.py" in relative or "financial_provider.py" in relative or "_financial_bridge.py" in relative
        records.append({"file":relative,"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"category":category,"status":"replaced" if replaced else "reference_only",
                        "reason":"Independent next-bar strategy adapter" if replaced else "Not agent-executable: reference signatures and numerical semantics are not independently validated; use reviewed operations for covered calculations",
                        "related_reviewed_operations":[name for name,op in OPERATIONS.items() if op.category==category],
                        "dependencies":dependencies,"public_entries":[{"name":name,"status":"replaced" if replaced else "not_registered"} for name in sorted(entries)]})
    return {"schema_version":1,"source_file_count":len(records),"public_entry_count":sum(len(x["public_entries"]) for x in records),"reviewed_operation_count":len(OPERATIONS),"files":records}

if __name__=="__main__":
    import sys
    sys.path.insert(0,str(ROOT))
    target=ROOT/"evaluation"/"analytics_coverage.json"
    target.write_text(json.dumps(build(),indent=2)+"\n",encoding="utf-8")
    print("Inventoried",build()["source_file_count"],"files")
