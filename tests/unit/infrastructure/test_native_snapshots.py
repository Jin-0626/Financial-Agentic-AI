import unittest
import pandas as pd
from orchestrator.tools.native_snapshots import prices_snapshot, statements_snapshot

class NativeNormalizationTests(unittest.TestCase):
    def test_adjusted_precision_and_bursa(self):
        frame=pd.DataFrame({"Close":[0.123456789,0.124456789,0.125456789],"Volume":[0,2,3]},index=pd.date_range("2025-01-01",periods=3))
        result=prices_snapshot(frame,org_id="local-org",ticker="0157.KL",currency="MYR")
        self.assertEqual(result["dataset"]["bars"][0]["adjusted_close"],0.123456789)
        self.assertEqual(result["unit_multiplier"],"1")
        with self.assertRaises(ValueError):
            prices_snapshot(frame,org_id="local-org",ticker="0157.KL",currency="")
        frame.index=[frame.index[0]]*3
        with self.assertRaises(ValueError):
            prices_snapshot(frame,org_id="local-org",ticker="0157.KL",currency="MYR")

    def test_statements_dates_negative_and_missing(self):
        frame=pd.DataFrame({pd.Timestamp("2024-12-31"):[1000,-200,float("nan")]},index=["Total Revenue","Net Income","EBITDA"])
        result=statements_snapshot([frame],org_id="local-org",ticker="0157.KL",currency="MYR")
        row=result["dataset"]["periods"][0]
        self.assertEqual(row["period"],"2024-12-31")
        self.assertEqual(row["accounts"]["net_income"],"-200.0")
        self.assertNotIn("ebitda",row["accounts"])
        conflicting=pd.DataFrame({pd.Timestamp("2024-12-31"):[999]},index=["Total Revenue"])
        with self.assertRaises(ValueError):
            statements_snapshot([frame,conflicting],org_id="local-org",ticker="0157.KL",currency="MYR")
