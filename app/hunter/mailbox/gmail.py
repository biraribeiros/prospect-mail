"""Leitor Gmail API (escopo gmail.readonly). Varredura completa por data ou incremental por historyId."""
import base64
from collections.abc import Callable, Iterator
from datetime import datetime

import httpx

from app.hunter.mailbox.base import MailboxReader, RawMessage
from app.hunter.mailbox.parse import parse_rfc822

API = "https://gmail.googleapis.com/gmail/v1/users/me"


class GmailAdapter(MailboxReader):
    def __init__(self, token_provider: Callable[[], str], http: httpx.Client | None = None,
                 history_id: str | None = None):
        self._token = token_provider
        self._http = http or httpx.Client(timeout=30)
        self._history_id = history_id
        self._max_history: int = int(history_id) if history_id else 0

    def _get(self, path: str, **params) -> dict:
        r = self._http.get(f"{API}{path}", params=params, headers={"Authorization": f"Bearer {self._token()}"})
        r.raise_for_status()
        return r.json()

    def _ids_by_query(self, since: datetime | None) -> Iterator[str]:
        params: dict = {"maxResults": 500}
        if since:
            params["q"] = f"after:{int(since.timestamp())}"
        while True:
            data = self._get("/messages", **params)
            for m in data.get("messages", []):
                yield m["id"]
            if not data.get("nextPageToken"):
                return
            params["pageToken"] = data["nextPageToken"]

    def _ids_by_history(self) -> Iterator[str] | None:
        """Incremental. Devolve None se o historyId expirou (404): aí é preciso varrer por data."""
        params: dict = {"startHistoryId": self._history_id, "historyTypes": "messageAdded", "maxResults": 500}
        ids: list[str] = []
        try:
            while True:
                data = self._get("/history", **params)
                self._max_history = max(self._max_history, int(data.get("historyId", 0) or 0))
                for h in data.get("history", []):
                    ids += [a["message"]["id"] for a in h.get("messagesAdded", [])]
                if not data.get("nextPageToken"):
                    break
                params["pageToken"] = data["nextPageToken"]
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        return iter(dict.fromkeys(ids))

    def scan(self, since: datetime | None = None) -> Iterator[RawMessage]:
        ids = self._ids_by_history() if self._history_id else None
        if ids is None:
            ids = self._ids_by_query(since)
        for mid in ids:
            data = self._get(f"/messages/{mid}", format="raw")
            self._max_history = max(self._max_history, int(data.get("historyId", 0) or 0))
            raw = base64.urlsafe_b64decode(data["raw"] + "=" * (-len(data["raw"]) % 4))
            hint = "enviado" if "SENT" in data.get("labelIds", []) else None
            msg = parse_rfc822(raw, hint)
            if msg and not (since and msg.date and msg.date < since):
                yield msg

    def cursor(self) -> dict:
        return {"historyId": str(self._max_history)} if self._max_history else {}
