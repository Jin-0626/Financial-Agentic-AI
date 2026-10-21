"""Keep Rust import parity fixtures aligned with the active Python contract."""

import hashlib
import json
from pathlib import Path
import unittest

from app.portfolio.service import import_trades

ROOT = Path(__file__).resolve().parents[3]


class RustImportFixtureParityTests(unittest.TestCase):
    def test_current_python_matches_rust_import_contract_fixtures(self):
        fixtures = json.loads(
            (ROOT / "crates/terminal-engine/tests/fixtures/portfolio-imports.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(fixtures, "Rust import fixture set must not be empty")
        for index, fixture in enumerate(fixtures):
            with self.subTest(index=index):
                data = json.loads(fixture["raw"])
                self.assertEqual(import_trades(data), fixture["trades"])
                canonical = json.dumps(["New", data, 0], sort_keys=True, allow_nan=False)
                self.assertEqual(
                    hashlib.sha256(canonical.encode()).hexdigest(), fixture["preview_hash"]
                )
