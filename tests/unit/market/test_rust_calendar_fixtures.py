"""Keep Rust release matching aligned with the current Python reference."""

import json
from pathlib import Path
import unittest
from app.market.calendar import verified_calendar_actual


class RustCalendarFixtureTests(unittest.TestCase):
    def test_calendar_actuals_match_current_python(self):
        path = (
            Path(__file__).resolve().parents[3]
            / "crates/terminal-providers/tests/fixtures/calendar-actuals.json"
        )
        cases = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(cases)
        for index, case in enumerate(cases):
            with self.subTest(index=index):
                self.assertEqual(
                    verified_calendar_actual(case["event"], case["observations"]), case["expected"]
                )
