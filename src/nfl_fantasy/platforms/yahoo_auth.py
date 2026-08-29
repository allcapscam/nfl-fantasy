"""Yahoo OAuth2, the authorization-code flow with an out-of-band redirect.

Yahoo is the only one of the three platforms that needs real OAuth. Sleeper
needs nothing and ESPN needs browser cookies, but Yahoo requires an app you
register yourself at https://developer.yahoo.com/apps/create.

`oob` ("out of band") is a valid redirect_uri, which is what makes this usable
from a terminal: Yahoo shows the user a code on screen instead of redirecting to
a server we would otherwise have to run over HTTPS.

Access tokens last an hour; refresh tokens last far longer. The token file is
refreshed automatically, so authorizing once should hold across a draft.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

AUTHORIZE_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
REDIRECT_URI = "oob"

TOKEN_PATH = Path("data/yahoo_token.json")

#: Refresh this many seconds before the token actually expires, so a long call
#: started just under the wire doesn't fail halfway through a draft.
EXPIRY_MARGIN = 120


class YahooToken(BaseModel):
    access_token: str
    refresh_token: str
    expires_at: float

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at - EXPIRY_MARGIN

    @classmethod
    def from_response(cls, payload: dict) -> YahooToken:
        return cls(
            access_token=payload["access_token"],
            refresh_token=payload["refresh_token"],
            expires_at=time.time() + float(payload.get("expires_in", 3600)),
        )

    def save(self, path: Path = TOKEN_PATH) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path = TOKEN_PATH) -> YahooToken | None:
        if not path.exists():
            return None
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class YahooCredentialsError(RuntimeError):
    """The app credentials are missing, so no Yahoo call can be made."""


def credentials() -> tuple[str, str]:
    """The registered app's client id and secret, from the environment."""
    client_id = os.environ.get("YAHOO_CLIENT_ID")
    client_secret = os.environ.get("YAHOO_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise YahooCredentialsError(
            "YAHOO_CLIENT_ID and YAHOO_CLIENT_SECRET are not set in .env.\n"
            "Register an app at https://developer.yahoo.com/apps/create with "
            "Fantasy Sports read permission, then run: draftbot auth yahoo"
        )
    return client_id, client_secret


def authorize_url(client_id: str) -> str:
    """Where the user goes to approve access."""
    query = urlencode(
        {"client_id": client_id, "redirect_uri": REDIRECT_URI, "response_type": "code"}
    )
    return f"{AUTHORIZE_URL}?{query}"


def _basic_auth_header(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _post_token(data: dict, client: httpx.Client | None = None) -> dict:
    client_id, client_secret = credentials()
    http = client or httpx.Client(timeout=30.0)
    response = http.post(
        TOKEN_URL,
        data={**data, "redirect_uri": REDIRECT_URI},
        headers={
            "Authorization": _basic_auth_header(client_id, client_secret),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Yahoo token request failed ({response.status_code}): {response.text[:300]}"
        )
    return response.json()


def exchange_code(code: str, client: httpx.Client | None = None) -> YahooToken:
    """Trade the pasted authorization code for tokens."""
    payload = _post_token(
        {"grant_type": "authorization_code", "code": code.strip()}, client
    )
    return YahooToken.from_response(payload)


def refresh(token: YahooToken, client: httpx.Client | None = None) -> YahooToken:
    """Get a fresh access token using the stored refresh token."""
    payload = _post_token(
        {"grant_type": "refresh_token", "refresh_token": token.refresh_token}, client
    )
    refreshed = YahooToken.from_response(payload)
    # Yahoo usually returns the same refresh token; keep the old one if not.
    if not payload.get("refresh_token"):
        refreshed.refresh_token = token.refresh_token
    return refreshed


def current_token(client: httpx.Client | None = None) -> YahooToken:
    """A valid access token, refreshing and re-saving if it has aged out."""
    token = YahooToken.load()
    if token is None:
        raise YahooCredentialsError(
            "Not authorized with Yahoo yet. Run: draftbot auth yahoo"
        )
    if token.expired:
        token = refresh(token, client)
        token.save()
    return token
