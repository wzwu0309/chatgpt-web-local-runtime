"""dsh-mcp-gateway package."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from typing import Any

from .exposure import Exposure, GenericExposure
from .harness_bridge import (
    HarnessBridgeClient,
    HarnessProjectionMixin,
    tool_result_to_mcp,
    watch_tool_catalog,
)
from .mcp_compat import disable_modern_subscriptions

try:  # resolved lazily via get_type_hints; only the MCP server path dereferences it
    from mcp.server.mcpserver.context import Context
except ImportError:  # pragma: no cover - optional server dependency
    Context = None

__version__ = "0.1.0"

_exec_log = logging.getLogger("dsh_mcp_gateway")


def build_mcp_server(
    harness_bridge: HarnessBridgeClient | None,
    *,
    exposure: Exposure | None = None,
    project_dsh_tools: bool = False,
    enable_chatgpt_web_companion: bool = False,
    auth_server_provider: Any | None = None,
    auth: Any | None = None,
    _server_cls: Any | None = None,
) -> Any:
    """Build one client-facing MCP surface over one DSH Harness bridge."""
    if project_dsh_tools and harness_bridge is None:
        raise ValueError("project_dsh_tools requires harness_bridge")
    if enable_chatgpt_web_companion and harness_bridge is None:
        raise ValueError("enable_chatgpt_web_companion requires harness_bridge")
    if harness_bridge is not None and exposure is None:
        raise ValueError("an exposure implementation is required when harness_bridge is supplied")

    try:
        from mcp.server import MCPServer
        from mcp.types import ToolAnnotations
    except ImportError as exc:  # pragma: no cover - installation boundary
        raise RuntimeError("MCP dependencies are unavailable; install dsh-mcp-gateway[server]") from exc

    read_only = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    consequential_control = ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    )

    server_cls = _server_cls or MCPServer
    harness_lifespan = None
    if harness_bridge is not None and project_dsh_tools:
        try:
            from mcp.shared.subscriptions import ToolsListChanged
        except ImportError as exc:  # pragma: no cover - server dependency boundary
            raise RuntimeError("MCP subscription support is unavailable") from exc

        @asynccontextmanager
        async def harness_lifespan(app: Any):
            async def publish_changed() -> None:
                await app._subscriptions.publish(ToolsListChanged())

            watcher = asyncio.create_task(
                watch_tool_catalog(harness_bridge, publish_changed),
                name="dsh-harness-tool-catalog-watcher",
            )
            try:
                yield {}
            finally:
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher

        server_cls = type(
            "HarnessProjectedMCPServer",
            (HarnessProjectionMixin, server_cls),
            {},
        )
    elif harness_bridge is not None and enable_chatgpt_web_companion:
        try:
            from mcp.shared.subscriptions import ResourcesListChanged, ToolsListChanged
        except ImportError as exc:  # pragma: no cover - server dependency boundary
            raise RuntimeError("MCP subscription support is unavailable") from exc

        @asynccontextmanager
        async def companion_refresh_lifespan(app: Any):
            async def announce_current_surface() -> None:
                # Modern MCP list-changed notifications have no replay.  During
                # the experimental companion's short reconnect window, publish
                # a bounded sequence so a ChatGPT subscription established just
                # after gateway restart still refetches current tool/resource
                # metadata.  Normal meta-only mode keeps subscriptions disabled.
                for delay in (1, 2, 4, 8, 16):
                    await asyncio.sleep(delay)
                    await app._subscriptions.publish(ToolsListChanged())
                    await app._subscriptions.publish(ResourcesListChanged())

            announcer = asyncio.create_task(
                announce_current_surface(),
                name="chatgpt-web-companion-surface-refresh",
            )
            try:
                yield {}
            finally:
                announcer.cancel()
                with suppress(asyncio.CancelledError):
                    await announcer

        harness_lifespan = companion_refresh_lifespan

    instructions = (
        (exposure or GenericExposure()).instructions
        + " When invoking through dsh_tool_call, arguments must include every property "
        "listed in the target tool's parameters.required from the catalog, using the exact property names in "
        "parameters.properties: read/write/edit take `file_path` (not `path`), bash takes `command` and "
        "`description`, glob/grep take `pattern`."
        + (
            " This server also projects compatible DSH ToolRuntime capabilities as first-class MCP tools as an "
            "explicit optional UX mode; extension availability must not depend on that projection."
            if project_dsh_tools
            else " This server is in meta-only mode, so DSH-internal tools are intentionally absent from the MCP tool list."
        )
    )
    extensions = None
    if enable_chatgpt_web_companion:
        from .chatgpt_web_companion import build_chatgpt_web_companion_apps

        extensions = [build_chatgpt_web_companion_apps(harness_bridge.call)]

    mcp = server_cls(
        "dsh-mcp-gateway",
        version=__version__,
        description="Expose DSH Harness capabilities to MCP clients.",
        instructions=instructions,
        auth_server_provider=auth_server_provider,
        auth=auth,
        lifespan=harness_lifespan,
        extensions=extensions,
    )

    if harness_bridge is not None and not project_dsh_tools and not enable_chatgpt_web_companion:
        disable_modern_subscriptions(mcp)
    if harness_bridge is not None:
        mcp._dsh_harness_bridge = harness_bridge
        mcp._dsh_exposure = exposure

        @mcp.tool(name="dsh_tool_catalog", annotations=read_only)
        def dsh_tool_catalog() -> dict[str, Any]:
            """List the current DSH ToolRuntime schemas exposed by installed DSH plugins."""
            tools = exposure.catalog_tools(harness_bridge)
            return {
                "tools": tools,
                "count": len(tools),
                "callContract": (
                    "dsh_tool_call.arguments must include every property in the target entry's "
                    "parameters.required, using the exact names in parameters.properties — e.g. "
                    "file_path (not path) for read/write/edit."
                ),
            }

        if exposure.exposes_workspace_parameter:

            @mcp.tool(name="dsh_tool_call", annotations=consequential_control, description=exposure.tool_call_description)
            def dsh_tool_call(
                name: str,
                arguments: dict[str, Any] | None = None,
                workspace: str | None = None,
                ctx: Context = None,
            ) -> Any:
                """Execute one discovered DSH tool through DSH's guarded ToolRuntime pipeline."""
                result = exposure.call_tool(harness_bridge, name, arguments, ctx, workspace=workspace)
                return tool_result_to_mcp(result)

        else:

            @mcp.tool(name="dsh_tool_call", annotations=consequential_control, description=exposure.tool_call_description)
            def dsh_tool_call(name: str, arguments: dict[str, Any] | None = None, ctx: Context = None) -> Any:
                """Execute one discovered DSH tool through DSH's guarded ToolRuntime pipeline."""
                result = exposure.call_tool(harness_bridge, name, arguments, ctx)
                return tool_result_to_mcp(result)

        @mcp.tool(name="dsh_skill_catalog", annotations=read_only)
        def dsh_skill_catalog(ctx: Context = None) -> dict[str, Any]:
            """List model-invocable skills from DSH's native SkillRegistry for the calling
            conversation's bound workspace."""
            skills = harness_bridge.skills(session_id=exposure.skill_session_id(ctx))
            return {"skills": skills, "count": len(skills)}

        @mcp.tool(name="dsh_skill_load", annotations=read_only)
        def dsh_skill_load(name: str, ctx: Context = None) -> dict[str, Any]:
            """Load one DSH community skill's instructions through the native SkillRegistry."""
            return {"skill": harness_bridge.load_skill(name, session_id=exposure.skill_session_id(ctx))}

    return mcp
