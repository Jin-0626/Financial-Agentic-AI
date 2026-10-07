import os
import shlex
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import sandbox as sb


class ToolProvisioningTests(unittest.TestCase):
    def provision(self, result=None, error=None):
        backend = Mock()
        backend.upload_files.return_value = [SimpleNamespace(error=None)]
        calls = []
        def execute(command, **kwargs):
            if "pip install" in command:
                calls.append(command)
                if error:
                    raise error
                return result or SimpleNamespace(exit_code=0, output="")
            return SimpleNamespace(exit_code=0, output="")
        backend.execute.side_effect = execute
        with patch.object(sb, "_collect_dir_files", side_effect=[[], [("/scripts/test.py", b"x")]]):
            outcome = sb._provision_sandbox(backend)
        return outcome, calls[0]

    def test_requirements_are_arguments_not_shell_redirections(self):
        requirements = ["pandas>=2.2.0", "numpy>=1.26.0", "example[extra]>=1,<2"]
        with patch.object(sb, "FINANCIAL_CORE_DEPS", requirements):
            (ok, _), command = self.provision()
        self.assertTrue(ok)
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
        for requirement in requirements:
            self.assertIn(requirement, tokens)
        self.assertNotIn(">", tokens)
        self.assertEqual(tokens[-3:], ["2", ">&", "1"])

    def test_empty_failed_execution_reports_exit_code(self):
        (ok, message), _ = self.provision(SimpleNamespace(exit_code=1, output=""))
        self.assertFalse(ok)
        self.assertIn("exit_code=1", message)
        self.assertIn("No command output", message)

    def test_unknown_exit_code_is_not_success(self):
        (ok, message), _ = self.provision(SimpleNamespace(exit_code=None, output=""))
        self.assertFalse(ok)
        self.assertIn("exit_code=None", message)

    def test_install_exception_reaches_status_without_credentials(self):
        with patch.dict(os.environ, {"EXAMPLE_API_KEY": "test-sensitive-value"}):
            (ok, message), _ = self.provision(error=RuntimeError("provider timeout test-sensitive-value"))
        self.assertFalse(ok)
        self.assertIn("provider timeout [redacted]", message)
        self.assertNotIn("test-sensitive-value", message)

    def test_bootstrap_failure_stops_before_install(self):
        backend = Mock()
        backend.upload_files.return_value = [SimpleNamespace(error=None)]
        backend.execute.side_effect = [SimpleNamespace(exit_code=0, output=""), SimpleNamespace(exit_code=1, output="No module named ensurepip")]
        with patch.object(sb, "_collect_dir_files", side_effect=[[], [("/scripts/test.py", b"x")]]):
            ok, message = sb._provision_sandbox(backend)
        self.assertFalse(ok)
        self.assertIn("Python/pip bootstrap failed", message)
        self.assertIn("No module named ensurepip", message)
        self.assertEqual(backend.execute.call_count, 2)
