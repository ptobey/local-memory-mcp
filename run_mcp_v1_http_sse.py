import base64
import hmac
import os
import sys
from pathlib import Path

from fastapi import FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request

# Add parent directory to path for package imports
sys.path.insert(0, str(Path(__file__).parent))

from src.mcp_server_v1 import (  # noqa: E402
    _allowed_hosts,
    _auth_mode,
    _auth_provider,
    mcp,
    reset_request_scope_hint,
    set_request_scope_hint,
)

app = FastAPI(
    title="Local Memory MCP v1",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


class _OAuthClientAuthError(Exception):
    def __init__(self, message: str):
        self.message = message


class _OAuthClientAuthenticator:
    def __init__(self, provider):
        self.provider = provider

    async def authenticate_request(self, request: Request):
        form_data = await request.form()
        client_id = form_data.get("client_id")
        auth_header = request.headers.get("Authorization", "")
        basic_client_id = None
        basic_client_secret = None

        if auth_header.startswith("Basic "):
            try:
                encoded = auth_header[6:]
                decoded = base64.b64decode(encoded).decode("utf-8")
                if ":" in decoded:
                    basic_client_id, basic_client_secret = decoded.split(":", 1)
            except Exception:
                raise _OAuthClientAuthError("Invalid Basic authentication header")

        if not client_id:
            client_id = basic_client_id
        if not client_id:
            raise _OAuthClientAuthError("Missing client_id")

        client = await self.provider.get_client(str(client_id))
        if not client:
            raise _OAuthClientAuthError("Invalid client_id")

        request_client_secret = None
        if basic_client_secret is not None:
            request_client_secret = basic_client_secret
        elif isinstance(form_data.get("client_secret"), str):
            request_client_secret = str(form_data.get("client_secret"))

        if client.client_secret:
            if not request_client_secret:
                raise _OAuthClientAuthError("Client secret is required")
            if not hmac.compare_digest(client.client_secret.encode(), request_client_secret.encode()):
                raise _OAuthClientAuthError("Invalid client_secret")

        return client


if _auth_provider is not None:
    from mcp.server.auth.handlers.token import TokenErrorResponse, TokenHandler
    from mcp.server.auth.json_response import PydanticJSONResponse
    from starlette.responses import RedirectResponse

    _token_handler = TokenHandler(_auth_provider, _OAuthClientAuthenticator(_auth_provider))

    @app.post("/token")
    async def token_endpoint(request: Request):
        try:
            return await _token_handler.handle(request)
        except _OAuthClientAuthError as exc:
            return PydanticJSONResponse(
                content=TokenErrorResponse(
                    error="unauthorized_client",
                    error_description=exc.message,
                ),
                status_code=401,
                headers={
                    "Cache-Control": "no-store",
                    "Pragma": "no-cache",
                },
            )

    # Some MCP clients (e.g. claude.ai) call /oauth/authorize and /oauth/token
    # instead of the RFC-8414-advertised /authorize and /token. Forward those
    # to the real handlers so those clients can complete the OAuth flow.
    @app.get("/oauth/authorize")
    async def oauth_authorize_alias(request: Request):
        query = request.url.query
        target = "/authorize" + (("?" + query) if query else "")
        return RedirectResponse(url=target, status_code=307)

    @app.post("/oauth/token")
    async def oauth_token_alias(request: Request):
        return await token_endpoint(request)


def _request_scope_key(request: Request) -> str:
    # Prefer explicit session-ish headers if present; fall back to client+UA.
    session_headers = (
        "x-session-id",
        "mcp-session-id",
        "x-request-id",
    )
    for header in session_headers:
        value = request.headers.get(header)
        if value:
            return f"{header}:{value}"
    client_host = getattr(request.client, "host", "unknown")
    user_agent = request.headers.get("user-agent", "")
    ua_hint = user_agent[:120]
    return f"{client_host}|{ua_hint}"


@app.middleware("http")
async def bind_request_scope_hint(request: Request, call_next):
    token = set_request_scope_hint(_request_scope_key(request))
    try:
        return await call_next(request)
    finally:
        reset_request_scope_hint(token)


# Mount FastMCP transport routes onto this app.
_sse_app = mcp.sse_app()
_streamable_http_app = mcp.streamable_http_app()

# We mount the streamable-HTTP ROUTES (below) rather than the whole sub-app, so
# that sub-app's lifespan never runs — and that lifespan is what starts the
# session manager's task group. Without it, POST /mcp raises
# "Task group is not initialized". Attach the session manager's run() to this
# app's lifespan (mirrors FastMCP.streamable_http_app's own lifespan). SSE uses
# a separate transport and works without this.
app.router.lifespan_context = lambda _app: mcp.session_manager.run()

_applied_middlewares: set[tuple[str, str, str]] = set()
for transport_app in (_sse_app, _streamable_http_app):
    for middleware in transport_app.user_middleware:
        if middleware.cls is TrustedHostMiddleware:
            continue
        middleware_args = getattr(middleware, "args", ())
        middleware_kwargs = getattr(middleware, "kwargs", {})
        middleware_key = (
            str(getattr(middleware.cls, "__module__", "")),
            str(getattr(middleware.cls, "__name__", "")),
            repr((middleware_args, middleware_kwargs)),
        )
        if middleware_key in _applied_middlewares:
            continue
        _applied_middlewares.add(middleware_key)
        app.add_middleware(middleware.cls, *middleware_args, **middleware_kwargs)


def _trusted_hosts_from_config() -> list[str]:
    cleaned: list[str] = []
    for raw in _allowed_hosts or []:
        host = str(raw or "").strip()
        if not host:
            continue
        if host == "*":
            return ["*"]
        # Config may include host patterns with :* (for transport-security checks).
        if host.endswith(":*"):
            host = host[:-2]
        if host and host not in cleaned:
            cleaned.append(host)
    for fallback in ["127.0.0.1", "localhost", "[::1]"]:
        if fallback not in cleaned:
            cleaned.append(fallback)
    return cleaned


_trusted_hosts = _trusted_hosts_from_config()
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_trusted_hosts)
_mounted_paths: set[str] = set()
for transport_app in (_sse_app, _streamable_http_app):
    for route in transport_app.routes:
        path = getattr(route, "path", "")
        if path in _mounted_paths:
            continue
        _mounted_paths.add(path)
        app.router.routes.append(route)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "healthy"}


def run_server() -> None:
    import uvicorn

    bind_host = (os.environ.get("MCP_BIND_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    try:
        bind_port = int(os.environ.get("MCP_BIND_PORT") or "8000")
    except ValueError:
        bind_port = 8000

    if bind_host not in {"127.0.0.1", "localhost", "::1"} and _auth_mode == "none":
        print("WARNING: Non-loopback bind with MCP_AUTH_MODE='none' is not recommended.")

    print("=" * 60)
    print("Local Memory MCP v1 - MCP HTTP/SSE Server")
    print("=" * 60)
    print(f"Auth Mode: {_auth_mode}")
    print(f"Trusted Hosts: {', '.join(_trusted_hosts)}")
    print(f"Streamable HTTP: http://{bind_host}:{bind_port}/mcp")
    print(f"SSE: http://{bind_host}:{bind_port}/sse")
    print(f"Messages: http://{bind_host}:{bind_port}/messages/")
    if _auth_provider is not None:
        print(f"OAuth Token: http://{bind_host}:{bind_port}/token")
    print(f"Health: http://{bind_host}:{bind_port}/health")
    print("=" * 60)
    uvicorn.run(app, host=bind_host, port=bind_port)


if __name__ == "__main__":
    run_server()
