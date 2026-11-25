from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from dsh_mcp_gateway.harness_bridge import HarnessBridgeClient


class _Response:
    def __init__(self, payload: object) -> None:
        self.body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        return self.body if size < 0 else self.body[:size]


class HarnessBridgeWireTests(unittest.TestCase):
    def test_binding_projection_and_envelope_fields_reach_the_bridge(self) -> None:
        seen = []

        def fake_urlopen(request, timeout):
            body = json.loads(request.data) if request.data else None
            seen.append((request.full_url, request.method, body))
            if request.full_url.endswith("/tools"):
                return _Response({"tools": []})
            return _Response({"isError": False, "value": {"ok": True}, "content": []})

        client = HarnessBridgeClient("http://127.0.0.1:3080")
        with patch("dsh_mcp_gateway.harness_bridge.urlopen", side_effect=fake_urlopen):
            self.assertEqual(client.tools(binding=False), [])
            client.call(
                "bash",
                {"command": "pwd"},
                session_id="conversation-1",
                workspace="/tmp/workspaces/example",
                binding=False,
            )

        self.assertEqual(
            seen,
            [
                ("http://127.0.0.1:3080/api/chatgpt-bridge/tools", "POST", {"binding": False}),
                (
                    "http://127.0.0.1:3080/api/chatgpt-bridge/call",
                    "POST",
                    {
                        "name": "bash",
                        "arguments": {"command": "pwd"},
                        "binding": False,
                        "sessionId": "conversation-1",
                        "workspace": "/tmp/workspaces/example",
                    },
                ),
            ],
        )

    def test_default_call_participates_in_binding_without_workspace_envelope(self) -> None:
        seen = []

        def fake_urlopen(request, timeout):
            seen.append(json.loads(request.data))
            return _Response({"isError": False, "value": {"ok": True}, "content": []})

        client = HarnessBridgeClient("http://127.0.0.1:3080")
        with patch("dsh_mcp_gateway.harness_bridge.urlopen", side_effect=fake_urlopen):
            client.call("bash", {"command": "pwd"}, session_id="conversation-1")

        self.assertEqual(
            seen,
            [
                {
                    "name": "bash",
                    "arguments": {"command": "pwd"},
                    "binding": True,
                    "sessionId": "conversation-1",
                }
            ],
        )
        self.assertNotIn("workspace", seen[0])

    def test_skill_requests_forward_session_identity_only_when_present(self) -> None:
        seen = []

        def fake_urlopen(request, timeout):
            body = json.loads(request.data) if request.data else None
            seen.append((request.full_url, request.method, body))
            if request.full_url.endswith("/skills"):
                return _Response({"skills": []})
            return _Response(
                {
                    "skill": {
                        "name": "example",
                        "description": "example",
                        "source": "runtime",
                        "provider": "test",
                        "content": "instructions",
                    }
                }
            )

        client = HarnessBridgeClient("http://127.0.0.1:3080")
        with patch("dsh_mcp_gateway.harness_bridge.urlopen", side_effect=fake_urlopen):
            client.skills()
            client.skills(session_id="conversation-1")
            client.load_skill("example", session_id="conversation-1")

        self.assertEqual(seen[0], ("http://127.0.0.1:3080/api/chatgpt-bridge/skills", "GET", None))
        self.assertEqual(
            seen[1],
            ("http://127.0.0.1:3080/api/chatgpt-bridge/skills", "POST", {"sessionId": "conversation-1"}),
        )
        self.assertEqual(seen[2][2], {"name": "example", "sessionId": "conversation-1"})


if __name__ == "__main__":
    unittest.main()
