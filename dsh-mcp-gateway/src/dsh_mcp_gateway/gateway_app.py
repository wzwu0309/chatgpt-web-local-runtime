"""Composition of OAuth, route-level services, and per-path MCP exposures."""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.routing import Route

from . import build_mcp_server
from .exposure import ChatgptExposure, Exposure, GenericExposure
from .harness_bridge import HarnessBridgeClient
from .oauth import (
    EmbeddedOAuthConfig,
    EmbeddedOAuthProvider,
    advertise_public_client_auth_methods,
    install_approval_body_limit,
    install_approval_route,
    install_oauth_form_body_limit,
    install_registration_body_limit,
)


def default_exposure_mounts(*, root_exposure: str = "generic") -> dict[str, Exposure]:
    """Return the production mount table.

    ``root_exposure`` exists only for the approved two-stage cutover: stage one
    deploys with ``chatgpt`` so /mcp does not change while operators migrate
    the connector, and stage two returns to the generic default.
    """
    if root_exposure not in {"generic", "chatgpt"}:
        raise ValueError("root_exposure must be 'generic' or 'chatgpt'")
    generic = GenericExposure()
    chatgpt = ChatgptExposure()
    return {
        "/chatgpt/mcp": chatgpt,
        "/gemini/mcp": generic,
        "/mcp": generic if root_exposure == "generic" else chatgpt,
    }


class RouteCollector:
    """Small custom_route-compatible collector for routes owned by the outer app."""

    def __init__(self) -> None:
        self.routes: list[Route] = []

    def custom_route(self, path: str, *, methods: list[str], name: str | None = None, include_in_schema: bool = True):
        def decorator(func):
            self.routes.append(Route(path, endpoint=func, methods=methods, name=name, include_in_schema=include_in_schema))
            return func

        return decorator


class EmbeddedOAuthGateway:
    """One OAuth provider serving multiple path-selected MCP exposure objects."""

    def __init__(
        self,
        harness_bridge: HarnessBridgeClient,
        config: EmbeddedOAuthConfig,
        *,
        mounts: dict[str, Exposure],
        project_dsh_tools: bool = False,
        enable_chatgpt_web_companion: bool = False,
    ) -> None:
        if harness_bridge is None:
            raise ValueError("harness_bridge is required")
        if not mounts:
            raise ValueError("at least one exposure mount is required")
        for path, exposure in mounts.items():
            if not path.startswith("/") or len(path) < 2 or path.endswith("/"):
                raise ValueError(f"invalid exposure mount path: {path!r}")
            if not isinstance(exposure, Exposure):
                raise TypeError(f"unsupported exposure implementation at {path!r}")

        try:
            from mcp.server.auth.settings import (
                AuthSettings,
                ClientRegistrationOptions,
                RevocationOptions,
            )
            from pydantic import AnyHttpUrl
        except ImportError as exc:  # pragma: no cover - installation boundary
            raise RuntimeError("MCP auth dependencies are unavailable; install dsh-mcp-gateway[server]") from exc

        self.harness_bridge = harness_bridge
        self.provider = EmbeddedOAuthProvider(config)
        self.mounts = dict(mounts)
        self.project_dsh_tools = project_dsh_tools
        self.enable_chatgpt_web_companion = enable_chatgpt_web_companion
        scopes = list(config.scopes)
        self.auth = AuthSettings(
            issuer_url=AnyHttpUrl(config.issuer_url),
            resource_server_url=AnyHttpUrl(config.resource_url),
            required_scopes=list(config.required_scopes),
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=scopes,
                default_scopes=scopes,
            ),
            revocation_options=RevocationOptions(enabled=True),
        )
        self._routes = RouteCollector()
        install_approval_route(self._routes, self.provider)

    @property
    def config(self) -> EmbeddedOAuthConfig:
        return self.provider.config

    def custom_route(self, path: str, *, methods: list[str], name: str | None = None, include_in_schema: bool = True):
        return self._routes.custom_route(path, methods=methods, name=name, include_in_schema=include_in_schema)

    def exposure_health(self) -> dict[str, Any]:
        """Health signals keyed by exposure implementation, collapsing shared instances."""
        health: dict[str, Any] = {}
        for exposure in self.mounts.values():
            exposure_health = exposure.health()
            if not exposure_health:
                continue
            key = exposure.name
            if key not in health:
                health[key] = exposure_health
        return health

    def build_app(
        self,
        *,
        json_response: bool = True,
        transport_security: Any | None = None,
        host: str = "127.0.0.1",
    ) -> Starlette:
        """Build the outer ASGI app without exposing mount composition to callers."""
        from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
        from mcp.server.auth.middleware.bearer_auth import (
            BearerAuthBackend,
            RequireAuthMiddleware,
        )
        from mcp.server.auth.provider import ProviderTokenVerifier
        from mcp.server.auth.routes import (
            build_resource_metadata_url,
            create_auth_routes,
            create_protected_resource_routes,
        )

        token_verifier = ProviderTokenVerifier(self.provider)
        required_scopes = list(self.auth.required_scopes or [])
        resource_metadata_url = None
        if self.auth.resource_server_url:
            resource_metadata_url = build_resource_metadata_url(self.auth.resource_server_url)

        child_apps: list[Starlette] = []
        routes: list[Route] = []
        for path, exposure in self.mounts.items():
            server = build_mcp_server(
                self.harness_bridge,
                exposure=exposure,
                project_dsh_tools=self.project_dsh_tools,
                enable_chatgpt_web_companion=self.enable_chatgpt_web_companion,
            )
            child = server.streamable_http_app(
                streamable_http_path=path,
                json_response=json_response,
                transport_security=transport_security,
                host=host,
            )
            child_apps.append(child)
            routes.append(
                Route(
                    path,
                    endpoint=RequireAuthMiddleware(child, required_scopes, resource_metadata_url),
                )
            )

        routes.extend(
            create_auth_routes(
                provider=self.provider,
                issuer_url=self.auth.issuer_url,
                service_documentation_url=self.auth.service_documentation_url,
                client_registration_options=self.auth.client_registration_options,
                revocation_options=self.auth.revocation_options,
                identity_assertion_enabled=self.auth.identity_assertion_enabled,
            )
        )
        if self.auth.resource_server_url:
            routes.extend(
                create_protected_resource_routes(
                    resource_url=self.auth.resource_server_url,
                    authorization_servers=[self.auth.issuer_url],
                    scopes_supported=list(self.auth.required_scopes or []),
                )
            )
        routes.extend(self._routes.routes)

        @asynccontextmanager
        async def lifespan(_app: Starlette):
            async with AsyncExitStack() as stack:
                for child in child_apps:
                    await stack.enter_async_context(child.router.lifespan_context(child))
                yield {}

        middleware = [
            Middleware(
                AuthenticationMiddleware,
                backend=BearerAuthBackend(token_verifier),
            ),
            Middleware(AuthContextMiddleware),
        ]
        app = Starlette(
            debug=False,
            routes=routes,
            middleware=middleware,
            lifespan=lifespan,
        )
        advertise_public_client_auth_methods(app, self.auth)
        install_registration_body_limit(
            app,
            max_bytes=self.config.max_registration_request_bytes,
        )
        install_approval_body_limit(app)
        install_oauth_form_body_limit(app)
        return app
