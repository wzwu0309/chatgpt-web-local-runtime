"""Client identity resolution as an ordered recognizer chain.

ChatGPT Web carries a stable per-conversation header; other MCP clients
(Gemini, Claude, ...) do not.  Gemini in particular re-initializes its
transport session nearly every turn — its backend is distributed and a
conversation's turns land on different nodes, each holding its own MCP
connection — so ``mcp-session-id`` is dead within minutes and can never
key a durable workspace binding.

Each recognizer inspects the request and either returns a
:class:`ClientIdentity` or ``None`` to defer to the next.  They are ordered
by specificity: an explicit client-supplied conversation header beats
platform heuristics, which beat the authenticated OAuth principal, which
beats the ephemeral transport session.  Adding support for another client
means inserting one recognizer — existing entries stay untouched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

_log = logging.getLogger("dsh_mcp_gateway")

# Recognizer outcomes, ordered chain position documented per recognizer.
PERSISTENCE_CONVERSATION = "conversation"  # stable per end-user conversation
PERSISTENCE_PRINCIPAL = "principal"  # shared per OAuth client installation
PERSISTENCE_EPHEMERAL = "ephemeral"  # transport-scoped, dies with the connection


@dataclass(frozen=True)
class ClientIdentity:
    """Who a call belongs to and how durable that identity is.

    ``key`` is the session identifier forwarded to the DSH bridge; workspace
    bindings are keyed by it verbatim.  ``persistence`` tells operators how
    much meaning a binding under this key carries — ``conversation``-grade
    keys isolate end-user conversations, ``principal``-grade keys are shared
    by everything authenticated as the same OAuth client, and
    ``ephemeral``-grade keys are effectively write-once.
    """

    client: str
    key: str
    persistence: str


def _openai_recognizer(headers: Any, access_token: Any, ctx: Any) -> ClientIdentity | None:
    """ChatGPT Web: explicit stable per-conversation header.  Highest
    specificity — a client that bothers to declare conversation identity
    always wins over inference."""
    session = headers.get("x-openai-session") if headers else None
    if session:
        return ClientIdentity(client="chatgpt", key=session, persistence=PERSISTENCE_CONVERSATION)
    return None


def _gemini_recognizer(headers: Any, access_token: Any, ctx: Any) -> ClientIdentity | None:
    """Gemini Apps custom-app client: bare ``Google`` user-agent, no
    conversation header.  Gemini's distributed backend scatters one
    conversation across many transport sessions, so the best durable key
    is the OAuth client installation itself — every Gemini conversation
    shares one workspace binding by design."""
    ua = (headers.get("user-agent") or "") if headers else ""
    if not ua.startswith("Google"):
        return None
    client_id = getattr(access_token, "client_id", None)
    key = f"gemini/{client_id}" if client_id else "gemini/shared"
    return ClientIdentity(client="gemini", key=key, persistence=PERSISTENCE_PRINCIPAL)


def _principal_recognizer(headers: Any, access_token: Any, ctx: Any) -> ClientIdentity | None:
    """Any other authenticated client: the OAuth client registration is the
    most durable identity available.  Shared-per-installation degradation
    beats the dead-on-arrival transport binding for clients that lack a
    conversation header."""
    client_id = getattr(access_token, "client_id", None)
    if client_id:
        return ClientIdentity(client="oauth", key=f"oauth/{client_id}", persistence=PERSISTENCE_PRINCIPAL)
    return None


_RECOGNIZERS = (_openai_recognizer, _gemini_recognizer, _principal_recognizer)


def _access_token_from_context() -> Any:
    """OAuth access token of the current request, when the SDK auth
    middleware populated the contextvar.  Never fail the call path for it."""
    try:
        from mcp.server.auth.middleware.auth_context import get_access_token

        return get_access_token()
    except Exception:  # noqa: BLE001 - optional dependency / unauthenticated edge
        return None


def resolve_client_identity(ctx: Any) -> ClientIdentity | None:
    """Resolve the calling client's identity through the recognizer chain.

    Falls back to the transport session (``mcp-session-id`` / SDK session)
    when nothing more durable exists — an ephemeral key preserves the
    pre-chain behavior for unauthenticated or exotic callers.  ``None``
    means no identity at all; the bridge treats it as the identity-less
    default workspace path it already has.
    """
    request = getattr(getattr(ctx, "request_context", None), "request", None)
    headers = getattr(request, "headers", None)
    access_token = _access_token_from_context()
    for recognizer in _RECOGNIZERS:
        identity = recognizer(headers, access_token, ctx)
        if identity is not None:
            return identity
    session_id = headers.get("mcp-session-id") if headers else None
    session_id = session_id or getattr(ctx, "session_id", None)
    if session_id:
        return ClientIdentity(client="unknown", key=session_id, persistence=PERSISTENCE_EPHEMERAL)
    return None
