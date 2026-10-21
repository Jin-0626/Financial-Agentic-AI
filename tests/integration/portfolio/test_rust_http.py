"""Differential metadata contracts; two disposable SQLite files, no upstream calls."""

import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.bootstrap.market_application import app

COMMAND = os.getenv("RUST_SERVER_TEST_COMMAND")


def without_timestamps(value):
    if isinstance(value, dict):
        return {
            key: without_timestamps(item)
            for key, item in value.items()
            if key not in {"saved_at", "export_date"}
        }
    if isinstance(value, list):
        return [without_timestamps(item) for item in value]
    return value


@unittest.skipUnless(COMMAND, "Set RUST_SERVER_TEST_COMMAND to a portfolio-enabled Rust server")
class RustPortfolioHttpParityTests(unittest.TestCase):
    def test_metadata_lifecycle_matches_python(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            local = {"APP_ENV": "local", "PORTFOLIO_ENABLED": "true", "RESEARCH_ENABLED": "false"}
            local.update(
                {
                    key: ""
                    for key in ("OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL", "OIDC_CLIENT_ID")
                }
            )
            environment = {
                **os.environ,
                **local,
                "HOST": "127.0.0.1",
                "PORT": str(port),
                "PORTFOLIO_SQLITE_PATH": str(root / "rust.sqlite3"),
            }
            process = subprocess.Popen(
                json.loads(COMMAND),
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            def native(method, path, body=None):
                data = json.dumps(body).encode() if body is not None else None
                request = Request(
                    f"http://127.0.0.1:{port}{path}",
                    data=data,
                    method=method,
                    headers={"Content-Type": "application/json"},
                )
                try:
                    response = urlopen(request, timeout=10)
                except HTTPError as error:
                    response = error
                with response:
                    return response.status, json.loads(response.read())

            try:
                deadline = time.monotonic() + 10
                while True:
                    if process.poll() is not None:
                        self.fail("Rust server exited before readiness")
                    try:
                        status, health = native("GET", "/api/health")
                        self.assertEqual(status, 200)
                        self.assertTrue(health["readiness"]["storage"])
                        break
                    except URLError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.05)
                with (
                    patch.dict(
                        os.environ, {**local, "PORTFOLIO_SQLITE_PATH": str(root / "python.sqlite3")}
                    ),
                    TestClient(app) as python,
                ):

                    def compare(method, path, body=None, status=200):
                        rust_status, rust_body = native(method, path, body)
                        response = (
                            python.request(method, path, json=body)
                            if body is not None
                            else python.request(method, path)
                        )
                        self.assertEqual((rust_status, response.status_code), (status, status))
                        self.assertEqual(
                            without_timestamps(rust_body), without_timestamps(response.json())
                        )
                        return rust_body

                    url = "/api/portfolio?org_id=org&user_id=alice&portfolio_id=fund"
                    compare("GET", url)
                    compare(
                        "PUT", url, {"name": "Initial", "currency": "MYR", "benchmark": "^KLSE"}
                    )
                    compare("GET", url)
                    compare(
                        "PUT",
                        url + "&revision=1",
                        {"name": "Edited", "currency": "MYR", "benchmark": "^KLSE"},
                    )
                    compare("PUT", url + "&revision=1", {"name": "Stale"}, status=409)
                    compare("GET", "/api/portfolio/list?org_id=org&user_id=alice")
                    compare("GET", "/api/portfolio?org_id=other&user_id=alice&portfolio_id=fund")
                    compare(
                        "GET", "/api/portfolio/export?org_id=org&user_id=alice&portfolio_id=fund"
                    )
                    compare("DELETE", url + "&revision=2")
                    compare("GET", url)
                    compare("DELETE", url, status=409)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
