import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import auth, portfolio_routes, routes
from app.portfolio_repository import MemoryPortfolioRepository

CONFIG = {
    "APP_ENV": "production",
    "OIDC_ISSUER": "https://issuer.example",
    "OIDC_JWKS_URL": "https://issuer.example/jwks",
    "OIDC_AUDIENCE": "terminal",
    "OIDC_CLIENT_ID": "terminal-web",
    "OIDC_ORG_CLAIM": "org_id",
    "PORTFOLIO_ENABLED": "true",
}


class IdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def token(self, **changes):
        claims = {
            "sub": "alice",
            "org_id": "org-a",
            "iss": CONFIG["OIDC_ISSUER"],
            "aud": "terminal",
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        }
        claims.update(changes)
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "test"})

    def setUp(self):
        app = FastAPI()
        app.include_router(auth.router, prefix="/api")
        app.include_router(portfolio_routes.router, prefix="/api")
        app.include_router(routes.router, prefix="/api")
        self.client = TestClient(app)
        self.env = patch.dict(os.environ, CONFIG)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.jwks = patch.object(
            auth,
            "jwks_client",
            return_value=SimpleNamespace(
                get_signing_key_from_jwt=lambda _: SimpleNamespace(
                    key=self.key.public_key()
                )
            ),
        )
        self.jwks.start()
        self.addCleanup(self.jwks.stop)
        self.repo = patch.object(
            portfolio_routes, "_repository", MemoryPortfolioRepository()
        )
        self.repo.start()
        self.addCleanup(self.repo.stop)

    def headers(self, **changes):
        return {"Authorization": "Bearer " + self.token(**changes)}

    def test_signature_claims_and_expiration_rejected(self):
        for changes in (
            {"aud": "other"},
            {"iss": "https://wrong.example"},
            {"exp": int(time.time()) - 100},
            {"org_id": None},
            {"sub": "other__tenant"},
        ):
            response = self.client.get("/api/auth/me", headers=self.headers(**changes))
            self.assertIn(response.status_code, (401, 403))
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        forged = jwt.encode({"exp": time.time() + 100}, other_key, algorithm="RS256")
        self.assertEqual(
            self.client.get(
                "/api/auth/me", headers={"Authorization": "Bearer " + forged}
            ).status_code,
            401,
        )
        self.assertEqual(self.client.get("/api/portfolio/list").status_code, 401)

    def test_portfolio_scope_comes_only_from_verified_identity(self):
        response = self.client.post(
            "/api/portfolio/create?org_id=spoof&user_id=victim",
            json={"name": "Owned"},
            headers=self.headers(),
        )
        self.assertEqual(response.status_code, 200)
        pid = response.json()["id"]
        own = self.client.get(
            "/api/portfolio/list?org_id=spoof&user_id=victim", headers=self.headers()
        ).json()
        self.assertEqual(own["portfolios"][0]["id"], pid)
        other = self.client.get(
            "/api/portfolio/list?org_id=org-a&user_id=alice",
            headers=self.headers(org_id="org-b"),
        ).json()
        self.assertEqual(other["portfolios"], [])
        other_user = self.client.get(
            "/api/portfolio/list", headers=self.headers(sub="bob")
        ).json()
        self.assertEqual(other_user["portfolios"], [])

    def test_research_thread_scope_and_body_cannot_override_identity(self):
        with (
            patch.object(routes, "_agent", object()),
            patch.object(
                routes, "_chat", AsyncMock(return_value={"success": True})
            ) as chat,
        ):
            response = self.client.post(
                "/api/chat",
                json={
                    "message": "Research",
                    "org_id": "spoof",
                    "user_id": "victim",
                    "thread_id": "new",
                },
                headers=self.headers(),
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                chat.call_args.args[1:4], ("org-a__alice__new", "alice", "org-a")
            )
            forbidden = self.client.post(
                "/api/chat",
                json={"message": "Research", "thread_id": "org-b__bob__owned"},
                headers=self.headers(),
            )
            self.assertEqual(forbidden.status_code, 403)

    def test_configuration_is_public_but_invalid_endpoints_fail_closed(self):
        self.assertTrue(self.client.get("/api/auth/config").json()["required"])
        with patch.dict(os.environ, {"OIDC_ISSUER": "http://unsafe.example"}):
            self.assertEqual(
                self.client.get("/api/auth/me", headers=self.headers()).status_code, 503
            )
