from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dsh_mcp_gateway.oauth import EmbeddedOAuthConfig, EmbeddedOAuthProvider
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull


def client_record(client_id: str) -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        redirect_uris=["http://127.0.0.1:9999/callback"],
        response_types=["code"],
        grant_types=["authorization_code"],
        token_endpoint_auth_method="none",
        scope="dsh:control",
    )


def config_for(path: Path, **overrides) -> EmbeddedOAuthConfig:
    values = {
        "issuer_url": "http://127.0.0.1:8000",
        "resource_url": "http://127.0.0.1:8000/mcp",
        "state_db": path / "oauth.sqlite3",
        "admin_pin": "test-admin-pin",
    }
    values.update(overrides)
    return EmbeddedOAuthConfig(**values)


class OAuthLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_token_default_ttl_is_180_days(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(config_for(Path(tmp).resolve()).refresh_token_ttl_s, 180 * 24 * 3600)

    async def test_resource_urls_must_share_the_resource_server_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, "share the resource server origin"):
            config_for(
                Path(tmp).resolve(),
                resource_urls=("https://evil.example/chatgpt/mcp",),
            )

    async def test_additional_mount_resources_are_accepted_for_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = config_for(
                Path(tmp).resolve(),
                resource_urls=("http://127.0.0.1:8000/chatgpt/mcp", "http://127.0.0.1:8000/gemini/mcp"),
            )
            provider = EmbeddedOAuthProvider(config)
            client = client_record("client-1")
            await provider.register_client(client)
            target = await provider.authorize(
                client,
                AuthorizationParams(
                    state="state",
                    scopes=["dsh:control"],
                    code_challenge="challenge",
                    redirect_uri="http://127.0.0.1:9999/callback",
                    redirect_uri_provided_explicitly=True,
                    resource="http://127.0.0.1:8000/chatgpt/mcp",
                ),
            )
            self.assertIn("/approve?request=", target)
            request_id = target.rsplit("request=", 1)[1]
            redirect = await provider.approve(request_id)
            code = parse_qs(urlparse(redirect).query)["code"][0]
            authorization_code = await provider.load_authorization_code(client, code)
            token = await provider.exchange_authorization_code(client, authorization_code)
            refresh_token = await provider.load_refresh_token(client, token.refresh_token)
            self.assertIsNotNone(refresh_token)
            self.assertEqual(refresh_token.resource, "http://127.0.0.1:8000/chatgpt/mcp")
            rotated = await provider.exchange_refresh_token(client, refresh_token, ["dsh:control"])
            self.assertTrue(rotated.access_token)
            self.assertTrue(rotated.refresh_token)

    async def test_unauthorized_registrations_are_reclaimed_while_authorized_ones_survive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = config_for(Path(tmp).resolve(), unauthorized_registration_ttl_s=1)
            provider = EmbeddedOAuthProvider(config)
            await provider.register_client(client_record("stale-unauthorized"))
            await provider.register_client(client_record("stale-authorized"))
            now = time.time()
            with sqlite3.connect(config.state_db) as db:
                db.execute("UPDATE oauth_clients SET created_at = ? WHERE client_id = ?", (now - 10, "stale-unauthorized"))
                db.execute(
                    "UPDATE oauth_clients SET created_at = ?, authorized_at = ? WHERE client_id = ?",
                    (now - 10, now - 5, "stale-authorized"),
                )
                db.commit()

            await provider.register_client(client_record("new-registration"))

            self.assertIsNone(await provider.get_client("stale-unauthorized"))
            self.assertIsNotNone(await provider.get_client("stale-authorized"))
            self.assertIsNotNone(await provider.get_client("new-registration"))

    async def test_legacy_registrations_are_preserved_as_authorized_during_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = (Path(tmp) / "oauth.sqlite3").resolve()
            legacy = client_record("legacy-client")
            with sqlite3.connect(db_path) as db:
                db.execute("CREATE TABLE oauth_clients (client_id TEXT PRIMARY KEY, client_json TEXT NOT NULL)")
                db.execute(
                    "INSERT INTO oauth_clients(client_id, client_json) VALUES (?, ?)",
                    ("legacy-client", legacy.model_dump_json()),
                )
                db.commit()

            config = config_for(Path(tmp).resolve())
            provider = EmbeddedOAuthProvider(config)
            self.assertIsNotNone(await provider.get_client("legacy-client"))
            with sqlite3.connect(db_path) as db:
                row = db.execute(
                    "SELECT created_at, authorized_at FROM oauth_clients WHERE client_id = ?",
                    ("legacy-client",),
                ).fetchone()
            self.assertIsNotNone(row[0])
            self.assertIsNotNone(row[1])

    async def test_owner_approval_marks_registration_as_authorized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = config_for(Path(tmp).resolve(), unauthorized_registration_ttl_s=1)
            provider = EmbeddedOAuthProvider(config)
            client = client_record("client-1")
            await provider.register_client(client)
            target = await provider.authorize(
                client,
                AuthorizationParams(
                    state="state",
                    scopes=["dsh:control"],
                    code_challenge="challenge",
                    redirect_uri="http://127.0.0.1:9999/callback",
                    redirect_uri_provided_explicitly=True,
                    resource=config.resource_url,
                ),
            )
            request_id = target.rsplit("request=", 1)[1]
            redirect = await provider.approve(request_id)
            self.assertIsNotNone(redirect)
            code = parse_qs(urlparse(redirect).query)["code"][0]
            authorization_code = await provider.load_authorization_code(client, code)
            self.assertIsNotNone(authorization_code)
            token = await provider.exchange_authorization_code(client, authorization_code)
            self.assertTrue(token.refresh_token)
            with sqlite3.connect(config.state_db) as db:
                authorized_at = db.execute(
                    "SELECT authorized_at FROM oauth_clients WHERE client_id = ?",
                    ("client-1",),
                ).fetchone()[0]
                resource, refresh_expires_at = db.execute(
                    "SELECT resource, expires_at FROM refresh_tokens WHERE token = ?",
                    (token.refresh_token,),
                ).fetchone()
            self.assertIsNotNone(authorized_at)
            self.assertEqual(resource, config.resource_url)
            self.assertAlmostEqual(refresh_expires_at - time.time(), 180 * 24 * 3600, delta=5)


if __name__ == "__main__":
    unittest.main()
