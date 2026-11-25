"""Client exposure implementations behind one small interface.

An exposure object owns everything that is allowed to differ between MCP
mounts: the shape of ``dsh_tool_call``, how catalog projection participates in
session binding, and which identity (if any) is forwarded to the DSH bridge.
Execution, validation, and capability policy remain downstream in the bridge.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from .identity import openai_conversation_session_id

_exec_log = logging.getLogger("dsh_mcp_gateway")


class MissingSessionMonitor:
    """Track sustained conversation-identity absence on the ChatGPT exposure."""

    def __init__(self, *, sustained_threshold: int = 3) -> None:
        if sustained_threshold <= 0:
            raise ValueError("sustained_threshold must be positive")
        self.sustained_threshold = sustained_threshold
        self._lock = threading.Lock()
        self._consecutive_missing = 0
        self._total_missing = 0
        self._total_observed = 0

    def observe(self, *, present: bool) -> tuple[bool, int]:
        """Observe one call; return (became_sustained, consecutive_missing)."""
        with self._lock:
            self._total_observed += 1
            if present:
                self._consecutive_missing = 0
                return False, 0
            self._total_missing += 1
            self._consecutive_missing += 1
            return (
                self._consecutive_missing == self.sustained_threshold,
                self._consecutive_missing,
            )

    def snapshot(self) -> dict[str, int | bool]:
        with self._lock:
            return {
                "sustainedMissing": self._consecutive_missing >= self.sustained_threshold,
                "consecutiveMissing": self._consecutive_missing,
                "totalMissing": self._total_missing,
                "totalObserved": self._total_observed,
            }



class Exposure:
    """Interface implemented by every mount-facing exposure object."""

    name: str
    exposes_workspace_parameter: bool
    tool_call_description: str
    instructions: str

    def catalog_tools(self, harness_bridge: Any) -> list[dict[str, Any]]:
        raise NotImplementedError

    def call_tool(
        self,
        harness_bridge: Any,
        name: str,
        arguments: dict[str, Any] | None,
        ctx: Any,
        *,
        workspace: str | None = None,
    ) -> Any:
        raise NotImplementedError

    def skill_session_id(self, ctx: Any) -> str | None:
        raise NotImplementedError

    def health(self) -> dict[str, Any]:
        return {}


class GenericExposure(Exposure):
    """Baseline exposure for clients without a durable conversation identity."""

    name = "generic"
    exposes_workspace_parameter = True

    instructions = """DSH is the harness authority and the connected MCP client is the reasoning agent. The stable meta-tools dsh_tool_catalog/dsh_tool_call and dsh_skill_catalog/dsh_skill_load are the correctness path for DSH community extensions: use them to discover and invoke capabilities even if the client keeps a frozen MCP tool snapshot. This exposure has no durable conversation workspace binding; dsh_tool_call.workspace is the optional per-call routing key. Pass the same workspace value on subsequent calls to keep working in one project. workspace selects the project/sandbox and is different from a tool-level workdir, which only changes one command's directory inside the selected project."""

    tool_call_description = """Execute one discovered DSH tool through DSH's guarded ToolRuntime pipeline.

`arguments` must satisfy the target tool's schema from dsh_tool_catalog — include
every property listed in its parameters.required under the exact names in
parameters.properties: read/write/edit take `file_path` (not `path`), bash takes
`command` and `description`. Missing or misnamed required properties are rejected
before execution.

Optional envelope parameter `workspace` selects the project workspace/sandbox for
this call. It is a routing key, not a tool argument, and is never passed inside
`arguments`. Omit it to use the launch default workspace. To keep working in one
project, pass the same workspace value on subsequent calls. The path must resolve
inside the configured allowed root; use bash from the default workspace to inspect
the available directory layout when unsure. Do not confuse `workspace` with a
tool-level `workdir`: use `workspace` to switch projects/sandboxes, and `workdir`
only for a per-command directory inside the already selected project.
"""

    def catalog_tools(self, harness_bridge: Any) -> list[dict[str, Any]]:
        return harness_bridge.tools(binding=False)

    def call_tool(
        self,
        harness_bridge: Any,
        name: str,
        arguments: dict[str, Any] | None,
        ctx: Any,
        *,
        workspace: str | None = None,
    ) -> Any:
        del ctx  # identity is deliberately not part of the generic envelope
        if workspace is not None and not workspace.strip():
            raise ValueError("workspace must be a non-empty path when supplied")
        return harness_bridge.call(
            name,
            arguments,
            session_id=None,
            workspace=workspace,
            binding=False,
        )

    def skill_session_id(self, ctx: Any) -> str | None:
        del ctx
        return None

    def health(self) -> dict[str, Any]:
        return {}


class ChatgptExposure(Exposure):
    """ChatGPT-specific exposure: the generic core plus the binding suite."""

    name = "chatgpt"
    exposes_workspace_parameter = False

    instructions = """DSH is the harness authority and ChatGPT is the reasoning agent. The stable meta-tools dsh_tool_catalog/dsh_tool_call and dsh_skill_catalog/dsh_skill_load are the correctness path for DSH community extensions: use them to discover and invoke capabilities even if the ChatGPT client keeps a frozen MCP tool snapshot. Calls on this exposure run under the conversation workspace binding: the launch default until changed with workspace_switch, then persistent for the whole conversation. Use workspace_status to inspect the active workspace and binding source."""

    tool_call_description = """Execute one discovered DSH tool through DSH's guarded ToolRuntime pipeline.

`arguments` must satisfy the target tool's schema from dsh_tool_catalog — include
every property listed in its parameters.required under the exact names in
parameters.properties: read/write/edit take `file_path` (not `path`), bash takes
`command` and `description`. Missing or misnamed required properties are rejected
before execution.

Calls run under this conversation's workspace binding — the launch
default until changed via workspace_switch, which then persists for
the whole conversation.
"""

    def __init__(self, *, missing_session_threshold: int = 3) -> None:
        self._missing_sessions = MissingSessionMonitor(sustained_threshold=missing_session_threshold)

    def catalog_tools(self, harness_bridge: Any) -> list[dict[str, Any]]:
        return harness_bridge.tools(binding=True)

    def call_tool(
        self,
        harness_bridge: Any,
        name: str,
        arguments: dict[str, Any] | None,
        ctx: Any,
        *,
        workspace: str | None = None,
    ) -> Any:
        if workspace is not None:
            raise ValueError("workspace envelope is unavailable on the ChatGPT exposure")
        session_id = openai_conversation_session_id(ctx)
        became_sustained, consecutive_missing = self._missing_sessions.observe(present=session_id is not None)
        if session_id is None and became_sustained:
            _exec_log.warning(
                "chatgpt exposure conversation identity missing for %s consecutive dsh_tool_call requests; "
                "workspace binding is degraded",
                consecutive_missing,
            )
        _exec_log.info(
            "dsh_tool_call client=chatgpt session=%s name=%s",
            (session_id or "-")[:32],
            name,
        )
        return harness_bridge.call(
            name,
            arguments,
            session_id=session_id,
            binding=True,
        )

    def skill_session_id(self, ctx: Any) -> str | None:
        return openai_conversation_session_id(ctx)

    def health(self) -> dict[str, Any]:
        return {"workspaceBinding": self._missing_sessions.snapshot()}
