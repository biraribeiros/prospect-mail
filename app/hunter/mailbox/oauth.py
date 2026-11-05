"""Renovação de tokens OAuth (Google e Microsoft) com cache em memória. Nunca registra o token em log."""
import time
from collections.abc import Callable

import httpx

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def _post(http: httpx.Client | None, url: str, data: dict) -> dict:
    client = http or httpx.Client(timeout=30)
    try:
        r = client.post(url, data=data)
        r.raise_for_status()
        return r.json()
    finally:
        if http is None:
            client.close()


def refresh_google_token(client_id: str, client_secret: str, refresh_token: str,
                         http: httpx.Client | None = None) -> dict:
    return _post(http, GOOGLE_TOKEN_URL, {
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": refresh_token, "grant_type": "refresh_token"})


def refresh_microsoft_token(tenant: str, client_id: str, client_secret: str, refresh_token: str,
                            scope: str = "https://graph.microsoft.com/Mail.Read offline_access",
                            http: httpx.Client | None = None) -> dict:
    return _post(http, f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token", {
        "client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token,
        "grant_type": "refresh_token", "scope": scope})


class OAuthToken:
    """Chamável que devolve um access_token válido, renovando 60 s antes de expirar."""

    def __init__(self, refresh_fn: Callable[[], dict]):
        self._refresh, self._token, self._expires = refresh_fn, None, 0.0

    def __call__(self) -> str:
        if not self._token or time.time() >= self._expires - 60:
            data = self._refresh()
            self._token = data["access_token"]
            self._expires = time.time() + int(data.get("expires_in", 3600))
        return self._token
