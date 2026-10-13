"""Architectural dependency rules for the current production packages."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]

class ModuleBoundaryTests(unittest.TestCase):
    def test_production_does_not_import_evaluation_or_sample_libraries(self):
        for package in ("app", "orchestrator", "contracts", "financial_analytics"):
            for path in (ROOT / package).rglob("*.py"):
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                    imports = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                    for name in imports:
                        with self.subTest(file=str(path.relative_to(ROOT)), dependency=name):
                            self.assertFalse(name == "evaluation" or name.startswith("evaluation."))
                            self.assertFalse(name.startswith(("scripts.Analytics", "scripts.strategies", "scripts.technicals", "scripts.financialanalysis")))

    def test_orchestration_and_shared_contracts_do_not_import_http_modules(self):
        for package in ("orchestrator", "contracts"):
            for path in (ROOT / package).rglob("*.py"):
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                    imports = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                    for name in imports:
                        with self.subTest(file=str(path.relative_to(ROOT)), dependency=name):
                            self.assertNotEqual(name, "app")
                            self.assertFalse(name.startswith("app.bootstrap"))
                            self.assertFalse(name.endswith(".routes"))
                            self.assertFalse(name.startswith("fastapi"))
