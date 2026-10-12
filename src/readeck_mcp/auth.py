"""OAuth login against a Readeck instance (authorization code flow with PKCE and a loopback redirect).

Readeck tokens don't expire, so a successful login is cached on disk and reused until revoked.
"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

SCOPES = "bookmarks:read bookmarks:write"
LOGIN_TIMEOUT = 300
TOKEN_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "readeck-mcp" / "tokens.json"


class LoginError(Exception):
    pass


def _log(message: str) -> None:
    # stdout carries the MCP protocol, so anything for humans goes to stderr.
    print(message, file=sys.stderr, flush=True)


def _load_tokens() -> dict[str, str]:
    try:
        return json.loads(TOKEN_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_tokens(tokens: dict[str, str]) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.touch(mode=0o600, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tokens, indent=2))


def cached_token(base_url: str) -> str | None:
    return _load_tokens().get(base_url)


def forget_token(base_url: str) -> str | None:
    tokens = _load_tokens()
    token = tokens.pop(base_url, None)
    _save_tokens(tokens)
    return token


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    # Set per login on a fresh subclass, so concurrent servers never share state.
    result: dict[str, str]
    received: threading.Event

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path != "/callback":
            self.send_error(404)
            return
        type(self).result = {k: v[0] for k, v in parse_qs(url.query).items()}
        ok = "code" in self.result
        error = self.result.get("error_description") or self.result.get("error", "unknown error")
        body = (
            "<h1>Readeck MCP is connected</h1><p>You can close this tab.</p>"
            if ok
            else f"<h1>Login failed</h1><p>{error}</p>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"<!doctype html><title>Readeck MCP</title>{body}".encode())
        type(self).received.set()

    def log_message(self, format: str, *args: object) -> None:
        pass


def login(base_url: str) -> str:
    """Open the Readeck authorization page in a browser, wait for approval and cache the token."""
    handler = type("Handler", (_CallbackHandler,), {"result": {}, "received": threading.Event()})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    redirect_uri = f"http://127.0.0.1:{server.server_port}/callback"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    try:
        with httpx.Client(base_url=base_url, headers={"Accept": "application/json"}, timeout=30) as client:
            response = client.post(
                "/api/oauth/client",
                json={
                    "client_name": "Readeck MCP",
                    "client_uri": f"{base_url}/",
                    "software_id": "readeck-mcp",
                    "software_version": "0.1.0",
                    "grant_types": ["authorization_code"],
                    "redirect_uris": [redirect_uri],
                },
            )
            if response.is_error:
                raise LoginError(f"Client registration failed (HTTP {response.status_code}): {response.text[:300]}")
            client_id = response.json()["client_id"]

            verifier = secrets.token_urlsafe(48)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            state = secrets.token_urlsafe(16)
            authorize_url = f"{base_url}/authorize?" + urlencode(
                {
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                    "scope": SCOPES,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "state": state,
                }
            )

            _log(f"Opening Readeck to authorize access. If no browser opens, visit:\n{authorize_url}")
            webbrowser.open(authorize_url)

            if not handler.received.wait(LOGIN_TIMEOUT):
                raise LoginError(
                    f"No response from the Readeck authorization page within {LOGIN_TIMEOUT // 60} minutes. "
                    f"Approve access at: {authorize_url}"
                )
            result = handler.result
            if result.get("state") != state:
                raise LoginError("Authorization response had a mismatched state; ignoring it.")
            if "code" not in result:
                reason = result.get("error_description") or result.get("error")
                raise LoginError(f"Authorization was not granted: {reason}")

            response = client.post(
                "/api/oauth/token",
                json={
                    "grant_type": "authorization_code",
                    "code": result["code"],
                    "code_verifier": verifier,
                },
            )
            if response.is_error:
                raise LoginError(f"Token exchange failed (HTTP {response.status_code}): {response.text[:300]}")
            token = response.json()["access_token"]
    finally:
        server.shutdown()
        server.server_close()

    tokens = _load_tokens()
    tokens[base_url] = token
    _save_tokens(tokens)
    _log(f"Logged in to {base_url}. Token saved to {TOKEN_FILE}")
    return token


def logout(base_url: str) -> bool:
    """Revoke and forget the cached token. Returns False if there was nothing to log out of."""
    token = forget_token(base_url)
    if not token:
        return False
    try:
        httpx.post(
            f"{base_url}/api/oauth/revoke",
            json={"token": token},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        _log(f"Token removed locally, but revoking it on the server failed: {exc}")
    return True
