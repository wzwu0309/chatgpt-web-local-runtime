from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dsh_mcp_gateway.cli import install_health_routes
from dsh_mcp_gateway.gateway_app import EmbeddedOAuthGateway, default_exposure_mounts
from dsh_mcp_gateway.oauth import EmbeddedOAuthConfig
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import LATEST_PROTOCOL_VERSION
from starlette.testclient import TestClient


class FakeHarnessBridge:
    base_url = "http://127.0.0.1:3080"

    def __init__(self) -> None:
        self.catalog_bindings: list[bool] = []
        self.calls: list[dict] = []

    def tools(self, *, timeout_s=None, binding: bool = True):
        self.catalog_bindings.append(binding)
        return []

    def skills(self, *, session_id=None):
        return []

    def load_skill(self, name: str, *, session_id=None):
        raise KeyError(name)

    def call(self, name: str, arguments=None, *, session_id=None, workspace=None, binding=True):
        self.calls.append(
            {
                "name": name,
                "arguments": arguments,
                "session_id": session_id,
                "workspace": workspace,
                "binding": binding,
            }
        )
        return {"isError": False, "content": [{"type": "text", "text": "ok"}], "value": {"ok": True}}


def complete_public_client_oauth(client: TestClient, config: EmbeddedOAuthConfig, *, resource_url: str | None = None) -> str:
    resource_url = resource_url or config.resource_url
    verifier = "v" * 48
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    redirect_uri = "https://example.com/client-callback"
    registration = client.post(
        "/register",
        json={
            "client_name": "mount-test-client",
            "redirect_uris": [redirect_uri],
            "response_types": ["code"],
            "grant_types": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_method": "none",
            "scope": "dsh:control offline_access",
        },
    )
    assert registration.status_code == 201, registration.text
    registered = registration.json()

    authorization = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": registered["client_id"],
            "redirect_uri": redirect_uri,
            "scope": "dsh:control offline_access",
            "state": "mount-test",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": resource_url,
        },
        follow_redirects=False,
    )
    assert authorization.status_code == 302, authorization.text
    request_id = parse_qs(urlparse(authorization.headers["location"]).query)["request"][0]
    approval = client.post(
        "/approve",
        data={"request": request_id, "pin": config.admin_pin, "action": "approve"},
        follow_redirects=False,
    )
    assert approval.status_code == 302, approval.text
    callback = parse_qs(urlparse(approval.headers["location"]).query)
    token = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "client_id": registered["client_id"],
            "code": callback["code"][0],
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            "resource": resource_url,
        },
    )
    assert token.status_code == 200, token.text
    return token.json()["access_token"]


def initialize_mcp(client: TestClient, path: str, access_token: str) -> dict[str, str]:
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "mount-test", "version": "1"},
        },
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    response = client.post(path, headers=headers, json=initialize)
    assert response.status_code == 200, response.text
    session_headers = {
        **headers,
        "mcp-session-id": response.headers["mcp-session-id"],
        "mcp-protocol-version": response.json()["result"]["protocolVersion"],
    }
    initialized = client.post(
        path,
        headers=session_headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert initialized.status_code == 202, initialized.text
    return session_headers


def rpc(client: TestClient, path: str, headers: dict[str, str], method: str, params: dict, request_id: int):
    response = client.post(
        path,
        headers=headers,
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "error" not in payload, json.dumps(payload)
    return payload["result"]


class GatewayMountTests(unittest.TestCase):
    def build_gateway(self, bridge: FakeHarnessBridge, state_root: Path) -> EmbeddedOAuthGateway:
        config = EmbeddedOAuthConfig(
            issuer_url="http://127.0.0.1",
            resource_url="http://127.0.0.1/mcp",
            resource_urls=("http://127.0.0.1/chatgpt/mcp", "http://127.0.0.1/gemini/mcp"),
            state_db=state_root / "oauth.sqlite3",
            admin_pin="test-admin-pin",
        )
        return EmbeddedOAuthGateway(
            bridge,
            config,
            mounts=default_exposure_mounts(),
        )

    def test_three_mounts_route_to_their_exposure_implementations(self) -> None:
        bridge = FakeHarnessBridge()
        with tempfile.TemporaryDirectory() as tmp:
            gateway = self.build_gateway(bridge, Path(tmp).resolve())
            security = TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=["127.0.0.1", "127.0.0.1:*"],
                allowed_origins=[],
            )
            app = gateway.build_app(
                json_response=True,
                transport_security=security,
                host="127.0.0.1",
            )
            with TestClient(app, base_url="http://127.0.0.1") as client:
                for path in ("/chatgpt/mcp", "/gemini/mcp", "/mcp"):
                    unauthenticated = client.post(
                        path,
                        headers={"Accept": "application/json", "Content-Type": "application/json"},
                        json={"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
                    )
                    self.assertEqual(unauthenticated.status_code, 401, path)
                    self.assertIn(
                        f'resource_metadata="http://127.0.0.1/.well-known/oauth-protected-resource{path}"',
                        unauthenticated.headers["www-authenticate"],
                    )

                token = complete_public_client_oauth(client, gateway.provider.config)
                surfaces = {}
                for path in ("/chatgpt/mcp", "/gemini/mcp", "/mcp"):
                    headers = initialize_mcp(client, path, token)
                    tools = rpc(client, path, headers, "tools/list", {}, 10)["tools"]
                    surfaces[path] = next(tool for tool in tools if tool["name"] == "dsh_tool_call")
                    rpc(
                        client,
                        path,
                        headers,
                        "tools/call",
                        {"name": "dsh_tool_catalog", "arguments": {}},
                        20,
                    )

                self.assertNotIn("workspace", surfaces["/chatgpt/mcp"]["inputSchema"]["properties"])
                self.assertIn("workspace", surfaces["/gemini/mcp"]["inputSchema"]["properties"])
                self.assertIn("workspace", surfaces["/mcp"]["inputSchema"]["properties"])

                gemini_headers = initialize_mcp(client, "/gemini/mcp", token)
                rpc(
                    client,
                    "/gemini/mcp",
                    gemini_headers,
                    "tools/call",
                    {
                        "name": "dsh_tool_call",
                        "arguments": {"name": "bash", "arguments": {}, "workspace": "/tmp/project-a"},
                    },
                    11,
                )
                chatgpt_headers = initialize_mcp(client, "/chatgpt/mcp", token)
                rpc(
                    client,
                    "/chatgpt/mcp",
                    {**chatgpt_headers, "x-openai-session": "conversation-1"},
                    "tools/call",
                    {"name": "dsh_tool_call", "arguments": {"name": "bash", "arguments": {}}},
                    12,
                )

        self.assertEqual(bridge.catalog_bindings, [True, False, False])
        self.assertEqual(
            bridge.calls,
            [
                {
                    "name": "bash",
                    "arguments": {},
                    "session_id": None,
                    "workspace": "/tmp/project-a",
                    "binding": False,
                },
                {
                    "name": "bash",
                    "arguments": {},
                    "session_id": "conversation-1",
                    "workspace": None,
                    "binding": True,
                },
            ],
        )

    def test_mounts_must_have_matching_oauth_resource_urls(self) -> None:
        config = EmbeddedOAuthConfig(
            issuer_url="http://127.0.0.1",
            resource_url="http://127.0.0.1/mcp",
            state_db=Path("/tmp/unused-oauth.sqlite3"),
            admin_pin="test-admin-pin",
        )
        with self.assertRaisesRegex(ValueError, "missing for exposure mounts"):
            EmbeddedOAuthGateway(FakeHarnessBridge(), config, mounts=default_exposure_mounts())

    def test_discovery_metadata_is_served_for_nested_mount_suffixes(self) -> None:
        bridge = FakeHarnessBridge()
        with tempfile.TemporaryDirectory() as tmp:
            gateway = self.build_gateway(bridge, Path(tmp).resolve())
            app = gateway.build_app(json_response=True, host="127.0.0.1")
            with TestClient(app, base_url="http://127.0.0.1") as client:
                authorization_metadata = client.get("/.well-known/oauth-authorization-server/chatgpt/mcp")
                arbitrary_suffix_metadata = client.get("/.well-known/oauth-authorization-server/future/client/path")
                protected_metadata = client.get("/.well-known/oauth-protected-resource/chatgpt/mcp")
                gemini_protected_metadata = client.get("/.well-known/oauth-protected-resource/gemini/mcp")
                root_protected_metadata = client.get("/.well-known/oauth-protected-resource/mcp")

        self.assertEqual(authorization_metadata.status_code, 200)
        self.assertIn("none", authorization_metadata.json()["token_endpoint_auth_methods_supported"])
        self.assertEqual(arbitrary_suffix_metadata.status_code, 200)
        self.assertEqual(protected_metadata.status_code, 200)
        self.assertEqual(protected_metadata.json()["resource"], "http://127.0.0.1/chatgpt/mcp")
        self.assertEqual(gemini_protected_metadata.status_code, 200)
        self.assertEqual(gemini_protected_metadata.json()["resource"], "http://127.0.0.1/gemini/mcp")
        self.assertEqual(root_protected_metadata.status_code, 200)
        self.assertEqual(root_protected_metadata.json()["resource"], "http://127.0.0.1/mcp")

    def test_nested_mount_resource_completes_oauth_and_opens_an_mcp_session(self) -> None:
        bridge = FakeHarnessBridge()
        with tempfile.TemporaryDirectory() as tmp:
            gateway = self.build_gateway(bridge, Path(tmp).resolve())
            security = TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=["127.0.0.1", "127.0.0.1:*"],
                allowed_origins=[],
            )
            app = gateway.build_app(json_response=True, transport_security=security, host="127.0.0.1")
            with TestClient(app, base_url="http://127.0.0.1") as client:
                token = complete_public_client_oauth(
                    client,
                    gateway.provider.config,
                    resource_url="http://127.0.0.1/chatgpt/mcp",
                )
                initialize_mcp(client, "/chatgpt/mcp", token)

    def test_missing_chatgpt_conversation_identity_becomes_visible_in_healthz(self) -> None:
        bridge = FakeHarnessBridge()
        with tempfile.TemporaryDirectory() as tmp:
            gateway = self.build_gateway(bridge, Path(tmp).resolve())
            install_health_routes(gateway, bridge)
            security = TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=["127.0.0.1", "127.0.0.1:*"],
                allowed_origins=[],
            )
            app = gateway.build_app(
                json_response=True,
                transport_security=security,
                host="127.0.0.1",
            )
            with TestClient(app, base_url="http://127.0.0.1") as client:
                token = complete_public_client_oauth(client, gateway.provider.config)
                headers = initialize_mcp(client, "/chatgpt/mcp", token)
                for request_id in (20, 21, 22):
                    rpc(
                        client,
                        "/chatgpt/mcp",
                        headers,
                        "tools/call",
                        {"name": "dsh_tool_call", "arguments": {"name": "bash", "arguments": {}}},
                        request_id,
                    )
                health = client.get("/healthz")

        self.assertEqual(health.status_code, 200)
        binding = health.json()["exposures"]["chatgpt"]["workspaceBinding"]
        self.assertIs(binding["sustainedMissing"], True)
        self.assertEqual(binding["consecutiveMissing"], 3)
        self.assertIs(health.json()["degraded"], True)


if __name__ == "__main__":
    unittest.main()
