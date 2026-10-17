"""Keep the checked-in environment template aligned with active configuration reads."""

import ast
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[3]


class ConfigurationContractsTests(unittest.TestCase):
    def test_template_covers_active_environment_reads_without_duplicate_defaults(self):
        template = (ROOT / ".env.example").read_text(encoding="utf-8")
        documented = set(re.findall(r"^\s*(?:#\s*)?([A-Z][A-Z_0-9]*)=", template, re.MULTILINE))
        defaults = re.findall(r"^([A-Z][A-Z_0-9]*)=", template, re.MULTILINE)
        self.assertEqual(len(defaults), len(set(defaults)), "duplicate environment defaults")
        reads = set()
        for package in ("app", "orchestrator", "contracts", "financial_analytics", "scripts"):
            for path in (ROOT / package).rglob("*.py"):
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and ast.unparse(node.func) in {"os.getenv", "os.environ.get"}
                        and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)
                    ):
                        reads.add(node.args[0].value)
                    if (
                        isinstance(node, ast.Subscript)
                        and ast.unparse(node.value) == "os.environ"
                        and isinstance(node.slice, ast.Constant)
                        and isinstance(node.slice.value, str)
                    ):
                        reads.add(node.slice.value)
        for package in ("crates", "frontend/src-tauri/src"):
            for path in (ROOT / package).rglob("*.rs"):
                reads.update(re.findall(r'env::var\("([A-Z_]+)"', path.read_text(encoding="utf-8")))
        # Platform directories are inherited from the OS, not application settings.
        self.assertFalse(reads - documented - {"LOCALAPPDATA", "XDG_DATA_HOME"})
        # These are consumed by third-party clients or by opt-in test commands.
        inherited = {"OLLAMA_API_KEY", "TEST_DB_URL", "ENGINE_TEST_COMMAND", "PLAYWRIGHT_CHANNEL"}
        self.assertFalse(documented - reads - inherited)
        self.assertNotIn("ALPHA_VANTAGE_API_KEY", documented)
        self.assertNotIn("OLLAMA_MODEL", documented)
