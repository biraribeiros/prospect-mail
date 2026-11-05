"""Leitor Microsoft Graph (escopo Mail.Read). Lê Caixa de Entrada e Itens Enviados."""
from collections.abc import Callable, Iterator
from datetime import datetime, timezone

import httpx

from app.hunter.mailbox.base import MailboxReader, RawMessage
from app.hunter.mailbox.parse import clean_addr, html_to_text, to_utc

API = "https://graph.microsoft.com/v1.0"
SELECT = "internetMessageId,sentDateTime,receivedDateTime,from,toRecipients,ccRecipients,subject,body,internetMessageHeaders"
FOLDERS = (("inbox", "receivedDateTime", None), ("sentitems", "sentDateTime", "enviado"))
KEEP = ("list-unsubscribe", "list-id", "precedence", "auto-submitted", "x-auto-response-suppress")


def _addrs(items) -> list[tuple[str | None, str]]:
    out = []
    for it in items or []:
        e = (it or {}).get("emailAddress", {})
        a = clean_addr(e.get("address"))
        if a:
            out.append(((e.get("name") or "").strip() or None, a))
    return out


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return to_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


class GraphAdapter(MailboxReader):
    def __init__(self, token_provider: Callable[[], str], http: httpx.Client | None = None):
        self._token = token_provider
        self._http = http or httpx.Client(timeout=30)

    def _get(self, url: str, params: dict | None = None) -> dict:
        r = self._http.get(url, params=params, headers={
            "Authorization": f"Bearer {self._token()}", "Prefer": 'outlook.body-content-type="text"'})
        r.raise_for_status()
        return r.json()

    def scan(self, since: datetime | None = None) -> Iterator[RawMessage]:
        for folder, date_field, hint in FOLDERS:
            url: str | None = f"{API}/me/mailFolders/{folder}/messages"
            params: dict | None = {"$select": SELECT, "$top": 50, "$orderby": f"{date_field} desc"}
            if since:
                params["$filter"] = f"{date_field} ge {since.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
            while url:
                data = self._get(url, params)
                for m in data.get("value", []):
                    sender = _addrs([m.get("from")])
                    if not sender:
                        continue
                    body = m.get("body") or {}
                    text = body.get("content", "")
                    if body.get("contentType", "").lower() == "html":
                        text = html_to_text(text)
                    yield RawMessage(
                        message_id=(m.get("internetMessageId") or "").strip("<>") or None,
                        date=_parse_dt(m.get(date_field) or m.get("receivedDateTime")),
                        from_addr=sender[0][1], from_name=sender[0][0],
                        to=_addrs(m.get("toRecipients")), cc=_addrs(m.get("ccRecipients")),
                        subject=m.get("subject") or "", body_text=text[:20_000],
                        headers={h["name"].lower(): str(h.get("value", "")).lower()
                                 for h in m.get("internetMessageHeaders", []) if h.get("name", "").lower() in KEEP},
                        direction_hint=hint,
                    )
                url, params = data.get("@odata.nextLink"), None  # nextLink já traz os parâmetros
