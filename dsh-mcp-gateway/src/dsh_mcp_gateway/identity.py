"""Conversation-identity extraction for binding-capable exposures.

Mount selection — not user-agent or OAuth-client heuristics — decides which
exposure implementation serves a client. This module contains only the wire
fact used by the ChatGPT exposure: ChatGPT's explicit per-conversation header.
"""

from __future__ import annotations

from typing import Any


def openai_conversation_session_id(ctx: Any) -> str | None:
    """Return ChatGPT's durable conversation id, or None when it is absent.

    OAuth principals and transport session ids are deliberately not fallback
    binding keys. The former collapses independent conversations into one
    binding; the latter makes workspace_switch appear to work while binding to
    a key that may never recur. Absence must reach the bridge as absence so
    binding is disabled loudly.
    """
    try:
        request_context = getattr(ctx, "request_context", None)
    except ValueError:
        # Context.request_context raises ValueError outside a wire request.
        return None
    request = getattr(request_context, "request", None)
    headers = getattr(request, "headers", None)
    session_id = headers.get("x-openai-session") if headers else None
    return session_id or None
