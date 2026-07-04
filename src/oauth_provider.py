from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    TokenError,
)
from mcp.shared.auth import InvalidRedirectUriError, OAuthClientInformationFull, OAuthToken


class OAuthClientAnyRedirect(OAuthClientInformationFull):
    def __init__(self, *args: Any, redirect_allowlist: Optional[List[str]] = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._redirect_allowlist = redirect_allowlist or []

    def validate_redirect_uri(self, redirect_uri):  # type: ignore[override]
        if redirect_uri is None:
            raise InvalidRedirectUriError("redirect_uri must be provided")
        if not self._redirect_allowlist:
            return redirect_uri
        for prefix in self._redirect_allowlist:
            if str(redirect_uri).startswith(prefix):
                return redirect_uri
        raise InvalidRedirectUriError("redirect_uri not allowed")


@dataclass
class InMemoryOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, Any, AccessToken]
):
    client: OAuthClientAnyRedirect
    token_ttl_seconds: int = 3600
    token_store_path: Optional[str] = None

    def __post_init__(self) -> None:
        self._auth_codes: Dict[str, AuthorizationCode] = {}
        self._access_tokens: Dict[str, AccessToken] = {}
        # Persist issued access tokens so a server restart doesn't log everyone
        # out (the store is otherwise purely in-memory). Auth codes stay
        # in-memory — they're single-use and expire in ~5 min.
        self._store_path = self.token_store_path or str(
            Path(__file__).resolve().parents[1] / ".oauth_tokens.json"
        )
        self._load_tokens()

    def _load_tokens(self) -> None:
        try:
            raw = json.loads(Path(self._store_path).read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return
        now = time.time()
        for tok, data in (raw.get("access_tokens") or {}).items():
            try:
                expires_at = data.get("expires_at")
                if expires_at is not None and float(expires_at) <= now:
                    continue
                self._access_tokens[tok] = AccessToken(**data)
            except Exception:
                continue

    def _save_tokens(self) -> None:
        now = time.time()
        out: Dict[str, Any] = {}
        for tok, at in self._access_tokens.items():
            expires_at = getattr(at, "expires_at", None)
            if expires_at is not None and float(expires_at) <= now:
                continue
            try:
                out[tok] = at.model_dump(mode="json")
            except Exception:
                out[tok] = {
                    "token": getattr(at, "token", tok),
                    "client_id": getattr(at, "client_id", ""),
                    "scopes": list(getattr(at, "scopes", []) or []),
                    "expires_at": getattr(at, "expires_at", None),
                    "resource": getattr(at, "resource", None),
                }
        try:
            path = Path(self._store_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"access_tokens": out}), encoding="utf-8")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            tmp.replace(path)
        except OSError:
            pass

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        if client_id == self.client.client_id:
            return self.client
        return None

    async def register_client(self, _client_info: OAuthClientInformationFull) -> None:
        raise NotImplementedError("Dynamic client registration is disabled")

    async def authorize(self, client: OAuthClientInformationFull, params) -> str:
        if client.client_id != self.client.client_id:
            raise AuthorizeError(error="unauthorized_client", error_description="Unknown client")
        code = secrets.token_urlsafe(32)
        expires_at = time.time() + 300
        auth_code = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=expires_at,
            client_id=client.client_id or "",
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        self._auth_codes[code] = auth_code
        from mcp.server.auth.provider import construct_redirect_uri

        return construct_redirect_uri(
            str(params.redirect_uri),
            code=code,
            state=params.state,
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        if client.client_id != self.client.client_id:
            return None
        return self._auth_codes.get(authorization_code)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if client.client_id != self.client.client_id:
            raise TokenError(error="unauthorized_client", error_description="Unknown client")
        access_token = secrets.token_urlsafe(32)
        expires_at = int(time.time() + self.token_ttl_seconds)
        token_scopes = authorization_code.scopes or []
        self._access_tokens[access_token] = AccessToken(
            token=access_token,
            client_id=client.client_id or "",
            scopes=token_scopes,
            expires_at=expires_at,
            resource=authorization_code.resource,
        )
        self._save_tokens()
        scope_str = " ".join(token_scopes) if token_scopes else None
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=self.token_ttl_seconds,
            scope=scope_str,
        )

    async def load_refresh_token(self, _client, _refresh_token):
        return None

    async def exchange_refresh_token(self, _client, _refresh_token, _scopes):
        raise TokenError(error="unsupported_grant_type", error_description="Refresh tokens not supported")

    async def load_access_token(self, token: str) -> AccessToken | None:
        return self._access_tokens.get(token)

    async def revoke_token(self, token):
        if isinstance(token, AccessToken):
            self._access_tokens.pop(token.token, None)
            self._save_tokens()
