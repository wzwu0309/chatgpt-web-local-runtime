from __future__ import annotations

import asyncio
import json
import unittest

from dsh_mcp_gateway import build_mcp_server
from dsh_mcp_gateway.exposure import ChatgptExposure, GenericExposure
from mcp.server.transport_security import TransportSecuritySettings
from starlette.testclient import TestClient


class FakeHarnessBridge:
    def __init__(self) -> None:
        self.catalog_bindings: list[bool] = []
        self.calls: list[dict] = []
        self.skill_session_ids: list[str | None] = []

    def tools(self, *, binding: bool = True):
        self.catalog_bindings.append(binding)
        return []

    def call(self, name, arguments=None, *, session_id=None, workspace=None, binding=True):
        self.calls.append(
            {
                "name": name,
                "arguments": arguments,
                "session_id": session_id,
                "workspace": workspace,
                "binding": binding,
            }
        )
        return {
            "isError": False,
            "content": [{"type": "text", "text": "ok"}],
            "value": {"ok": True},
        }

    def tool_revision_token(self):
        return ("fake-bridge", 0)

    def skills(self, *, session_id=None):
        self.skill_session_ids.append(session_id)
        return []

    def load_skill(self, name: str, *, session_id=None):
        self.skill_session_ids.append(session_id)
        return {
            "name": name,
            "source": "test",
            "provider": "test",
            "description": "test skill",
            "content": "skill instructions",
        }


def gateway_test_client(server, path: str = "/mcp") -> TestClient:
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1", "127.0.0.1:*"],
        allowed_origins=[],
    )
    app = server.streamable_http_app(
        streamable_http_path=path,
        json_response=True,
        host="127.0.0.1",
        transport_security=security,
    )
    return TestClient(app, base_url="http://127.0.0.1")


class McpSessionHeaders(dict):
    initialize_result: dict


def initialize_mcp(client: TestClient, path: str = "/mcp") -> McpSessionHeaders:
    response = client.post(
        path,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "exposure-test", "version": "1"},
            },
        },
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    assert response.status_code == 200, response.text
    headers = McpSessionHeaders({"Accept": "application/json", "Content-Type": "application/json"})
    headers.initialize_result = response.json()["result"]
    session_id = response.headers.get("mcp-session-id")
    if session_id:
        headers["mcp-session-id"] = session_id
    initialized = client.post(
        path,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=headers,
    )
    assert initialized.status_code == 202, initialized.text
    return headers


def rpc(client: TestClient, path: str, headers: dict[str, str], method: str, params: dict, request_id: int = 2):
    response = client.post(
        path,
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "error" not in payload, json.dumps(payload)
    return payload["result"]


class ExposureWireTests(unittest.TestCase):
    def test_generic_exposure_exposes_workspace_and_forwards_it_as_envelope(self) -> None:
        bridge = FakeHarnessBridge()
        server = build_mcp_server(bridge, exposure=GenericExposure())
        with gateway_test_client(server) as client:
            headers = initialize_mcp(client)
            tools = rpc(client, "/mcp", headers, "tools/list", {})["tools"]
            tool_call = next(tool for tool in tools if tool["name"] == "dsh_tool_call")

            self.assertIn("workspace", tool_call["inputSchema"]["properties"])
            self.assertNotIn("workspace", tool_call["inputSchema"].get("required", []))
            self.assertIn("allowed root", tool_call["description"])
            self.assertIn("workdir", tool_call["description"])

            rpc(
                client,
                "/mcp",
                headers,
                "tools/call",
                {
                    "name": "dsh_tool_call",
                    "arguments": {
                        "name": "bash",
                        "arguments": {"command": "pwd", "description": "probe"},
                        "workspace": "/tmp/project-a",
                    },
                },
                request_id=3,
            )

        self.assertEqual(
            bridge.calls,
            [
                {
                    "name": "bash",
                    "arguments": {"command": "pwd", "description": "probe"},
                    "session_id": None,
                    "workspace": "/tmp/project-a",
                    "binding": False,
                }
            ],
        )

    def test_generic_catalog_requests_the_non_binding_projection(self) -> None:
        bridge = FakeHarnessBridge()
        server = build_mcp_server(bridge, exposure=GenericExposure())
        with gateway_test_client(server) as client:
            headers = initialize_mcp(client)
            rpc(
                client,
                "/mcp",
                headers,
                "tools/call",
                {"name": "dsh_tool_catalog", "arguments": {}},
            )

        self.assertEqual(bridge.catalog_bindings, [False])

    def test_chatgpt_exposure_hides_workspace_and_forwards_only_conversation_identity(self) -> None:
        bridge = FakeHarnessBridge()
        server = build_mcp_server(bridge, exposure=ChatgptExposure())
        with gateway_test_client(server) as client:
            headers = initialize_mcp(client)
            tools = rpc(client, "/mcp", headers, "tools/list", {})["tools"]
            tool_call = next(tool for tool in tools if tool["name"] == "dsh_tool_call")
            self.assertNotIn("workspace", tool_call["inputSchema"]["properties"])

            rpc(
                client,
                "/mcp",
                {**headers, "x-openai-session": "conversation-1"},
                "tools/call",
                {"name": "dsh_tool_call", "arguments": {"name": "bash", "arguments": {}}},
                request_id=3,
            )
            rpc(
                client,
                "/mcp",
                headers,
                "tools/call",
                {"name": "dsh_tool_call", "arguments": {"name": "bash", "arguments": {}}},
                request_id=4,
            )

        self.assertEqual(bridge.calls[0]["session_id"], "conversation-1")
        self.assertIs(bridge.calls[0]["binding"], True)
        self.assertIsNone(bridge.calls[0]["workspace"])
        # The MCP transport session is explicitly not a durable conversation
        # identity: when ChatGPT's conversation header is absent the bridge
        # must see identity absence so binding is disabled instead of collapsing
        # or write-once binding under a doomed key.
        self.assertIsNone(bridge.calls[1]["session_id"])

    def test_chatgpt_catalog_requests_the_binding_projection(self) -> None:
        bridge = FakeHarnessBridge()
        server = build_mcp_server(bridge, exposure=ChatgptExposure())
        with gateway_test_client(server) as client:
            headers = initialize_mcp(client)
            rpc(
                client,
                "/mcp",
                headers,
                "tools/call",
                {"name": "dsh_tool_catalog", "arguments": {}},
            )

        self.assertEqual(bridge.catalog_bindings, [True])

    def test_skill_calls_follow_the_same_identity_axis(self) -> None:
        generic_bridge = FakeHarnessBridge()
        generic_server = build_mcp_server(generic_bridge, exposure=GenericExposure())
        with gateway_test_client(generic_server) as client:
            headers = initialize_mcp(client)
            rpc(client, "/mcp", headers, "tools/call", {"name": "dsh_skill_catalog", "arguments": {}}, request_id=3)
            rpc(
                client,
                "/mcp",
                headers,
                "tools/call",
                {"name": "dsh_skill_load", "arguments": {"name": "example"}},
                request_id=4,
            )

        chatgpt_bridge = FakeHarnessBridge()
        chatgpt_server = build_mcp_server(chatgpt_bridge, exposure=ChatgptExposure())
        with gateway_test_client(chatgpt_server) as client:
            headers = initialize_mcp(client)
            rpc(
                client,
                "/mcp",
                {**headers, "x-openai-session": "conversation-1"},
                "tools/call",
                {"name": "dsh_skill_catalog", "arguments": {}},
                request_id=3,
            )
            rpc(
                client,
                "/mcp",
                {**headers, "x-openai-session": "conversation-1"},
                "tools/call",
                {"name": "dsh_skill_load", "arguments": {"name": "example"}},
                request_id=4,
            )

        self.assertEqual(generic_bridge.skill_session_ids, [None, None])
        self.assertEqual(chatgpt_bridge.skill_session_ids, ["conversation-1", "conversation-1"])

    def test_initialize_instructions_are_client_neutral_and_exposure_specific(self) -> None:
        generic_server = build_mcp_server(FakeHarnessBridge(), exposure=GenericExposure())
        with gateway_test_client(generic_server) as client:
            generic_headers = initialize_mcp(client)
        generic_instructions = generic_headers.initialize_result["instructions"]
        self.assertNotIn("ChatGPT", generic_instructions)
        self.assertIn("workspace", generic_instructions)
        self.assertIn("workdir", generic_instructions)

        chatgpt_server = build_mcp_server(FakeHarnessBridge(), exposure=ChatgptExposure())
        with gateway_test_client(chatgpt_server) as client:
            chatgpt_headers = initialize_mcp(client)
        chatgpt_instructions = chatgpt_headers.initialize_result["instructions"]
        self.assertIn("workspace_switch", chatgpt_instructions)

    def test_blank_workspace_is_rejected_instead_of_silently_falling_back(self) -> None:
        bridge = FakeHarnessBridge()
        exposure = GenericExposure()
        with self.assertRaisesRegex(ValueError, "non-empty"):
            exposure.call_tool(bridge, "bash", {}, None, workspace="  ")
        self.assertEqual(bridge.calls, [])

    def test_projected_surface_uses_the_same_exposure_projection_and_envelope(self) -> None:
        class ProjectedBridge(FakeHarnessBridge):
            def tools(self, *, binding: bool = True):
                self.catalog_bindings.append(binding)
                return [
                    {
                        "name": "bash",
                        "description": "run a command",
                        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                    }
                ]

        bridge = ProjectedBridge()
        server = build_mcp_server(bridge, exposure=GenericExposure(), project_dsh_tools=True)
        tools = asyncio.run(server.list_tools())
        self.assertIn("bash", {tool.name for tool in tools})
        asyncio.run(server.call_tool("bash", {}))

        self.assertEqual(bridge.catalog_bindings, [False])
        self.assertEqual(bridge.calls[0]["binding"], False)
        self.assertIsNone(bridge.calls[0]["session_id"])

    def test_projected_chatgpt_call_forwards_conversation_identity_and_binding(self) -> None:
        class ProjectedBridge(FakeHarnessBridge):
            def tools(self, *, binding: bool = True):
                self.catalog_bindings.append(binding)
                return [
                    {
                        "name": "bash",
                        "description": "run a command",
                        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                    }
                ]

        bridge = ProjectedBridge()
        server = build_mcp_server(bridge, exposure=ChatgptExposure(), project_dsh_tools=True)
        with gateway_test_client(server) as client:
            headers = initialize_mcp(client)
            rpc(
                client,
                "/mcp",
                {**headers, "x-openai-session": "conversation-1"},
                "tools/call",
                {"name": "bash", "arguments": {}},
                request_id=3,
            )

        self.assertEqual(bridge.calls[0]["session_id"], "conversation-1")
        self.assertIs(bridge.calls[0]["binding"], True)


if __name__ == "__main__":
    unittest.main()
