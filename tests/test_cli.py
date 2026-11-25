from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from dsh_mcp_gateway.cli import build_parser, main
from dsh_mcp_gateway.exposure import ChatgptExposure, GenericExposure


class CliTests(unittest.TestCase):
    def harness_args(self, *extra: str) -> list[str]:
        return [
            "--public-base-url",
            "https://gateway.example.com",
            "--dsh-harness-url",
            "http://127.0.0.1:3080",
            *extra,
        ]

    def test_root_exposure_defaults_to_generic_and_stage_one_override_is_available(self) -> None:
        args = build_parser().parse_args(self.harness_args())
        self.assertEqual(args.root_mcp_exposure, "generic")
        transitional = build_parser().parse_args(self.harness_args("--root-mcp-exposure", "chatgpt"))
        self.assertEqual(transitional.root_mcp_exposure, "chatgpt")

    def test_main_builds_multi_mount_gateway_app(self) -> None:
        captured = {}

        class FakeGateway:
            def __init__(self, harness_bridge, config, *, mounts, project_dsh_tools, enable_chatgpt_web_companion):
                captured["mounts"] = mounts
                captured["config"] = config
                self.provider = Mock()

            def custom_route(self, *args, **kwargs):
                def decorator(func):
                    return func

                return decorator

            def build_app(self, **kwargs):
                captured["build_kwargs"] = kwargs
                return object()

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {"DSH_MCP_GATEWAY_ADMIN_PIN": "test-admin-pin"}, clear=False),
            patch("dsh_mcp_gateway.cli.EmbeddedOAuthGateway", FakeGateway),
            patch("uvicorn.Server") as server_cls,
        ):
            server_cls.return_value.run.return_value = None
            result = main(
                self.harness_args(
                    "--state-dir",
                    str(Path(tmp).resolve()),
                    "--root-mcp-exposure",
                    "chatgpt",
                )
            )

        self.assertEqual(result, 0)
        self.assertIsInstance(captured["mounts"]["/chatgpt/mcp"], ChatgptExposure)
        self.assertIsInstance(captured["mounts"]["/gemini/mcp"], GenericExposure)
        self.assertIs(captured["mounts"]["/mcp"], captured["mounts"]["/chatgpt/mcp"])


if __name__ == "__main__":
    unittest.main()
