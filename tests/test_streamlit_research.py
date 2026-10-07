import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest
from test_structured_research import report_data
from research_schema import AnalysisReport

ROOT = Path(__file__).resolve().parents[1]


class ResearchUIRegressionTests(unittest.TestCase):
    def test_launch_without_project_root_on_import_path(self):
        code = """
import importlib.util
import sys
from unittest.mock import Mock, patch
from streamlit.testing.v1 import AppTest
assert importlib.util.find_spec("research_schema") is None
response = Mock(status_code=200)
response.json.return_value = []
with patch("requests.get", return_value=response):
    app = AppTest.from_file(sys.argv[1], default_timeout=10).run()
assert not app.exception, [item.message for item in app.exception]
assert len(app.chat_input) == 1
assert "app" not in sys.modules, "Frontend must not initialize the FastAPI package"
print("Isolated Streamlit launch passed")
"""
        with tempfile.TemporaryDirectory() as working_directory:
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-c", code, str(ROOT / "static/app.py")],
                cwd=working_directory, capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Isolated Streamlit launch passed", result.stdout)

    def run_chat(self, events, research_mode=True):
        get = Mock(status_code=200)
        get.json.return_value = []
        response = Mock()
        response.iter_lines.return_value = ["data: " + json.dumps(event) for event in events]
        with patch("requests.get", return_value=get), patch("requests.post", return_value=response):
            app = AppTest.from_file(str(ROOT / "static/app.py"), default_timeout=10).run()
            if not research_mode:
                app.checkbox[0].uncheck().run()
            app.chat_input[0].set_value("Research").run()
        self.assertFalse(app.exception)
        return app

    def test_error_clears_provisional_json_and_does_not_save_answer(self):
        app = self.run_chat([
            {"type": "token", "content": '{"answer": "Rejected raw JSON"}'},
            {"type": "error", "message": "Schema validation failed"},
        ])
        self.assertTrue(app.error)
        self.assertFalse(any(m["role"] == "assistant" for m in app.session_state["messages"]))
        self.assertFalse(any("Rejected raw JSON" in element.value for element in app.markdown))

    def test_done_renders_markdown_from_validated_report(self):
        app = self.run_chat([
            {"type": "token", "content": "Provisional draft"},
            {"type": "report", "structured_response": report_data()},
            {"type": "done", "reply": "Incorrect legacy reply"},
        ])
        self.assertEqual(app.session_state["messages"][-1]["content"], AnalysisReport.model_validate(report_data()).answer)
        self.assertTrue(any("Executive Summary" in element.value for element in app.markdown))
        self.assertFalse(app.error)

    def test_truncated_stream_does_not_save_provisional_answer(self):
        app = self.run_chat([{"type": "token", "content": "Incomplete report"}])
        self.assertTrue(app.error)
        self.assertFalse(any(m["role"] == "assistant" for m in app.session_state["messages"]))

    def test_ordinary_chat_saves_readable_reply_without_report(self):
        app = self.run_chat([{"type": "token", "content": "Hello"}, {"type": "done", "reply": "Hello"}], research_mode=False)
        self.assertFalse(app.error)
        self.assertEqual(app.session_state["messages"][-1]["content"], "Hello")

    def test_interrupted_stream_does_not_save_completed_report(self):
        app = self.run_chat([{"type": "interrupted", "interruptions": [{"id": "approval", "value": "approve"}]}])
        self.assertTrue(app.info)
        self.assertFalse(any(m["role"] == "assistant" for m in app.session_state["messages"]))

    def test_done_without_report_is_not_success(self):
        app = self.run_chat([{"type": "done", "reply": "Unvalidated"}])
        self.assertTrue(app.error)
        self.assertFalse(any(m["role"] == "assistant" for m in app.session_state["messages"]))


if __name__ == "__main__":
    unittest.main()
