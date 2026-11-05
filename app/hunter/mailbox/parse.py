"""Conversão de email bruto (RFC 822) em RawMessage — compartilhada por mbox, IMAP e Gmail."""
import html
import re
from datetime import datetime, timezone
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr

from app.hunter.mailbox.base import RawMessage

EMAIL_RE = re.compile(r"^[^@\s<>\"',;]+@[^@\s<>\"',;]+\.[^@\s<>\"',;]+$")
MAX_BODY = 20_000
KEEP_HEADERS = ("list-unsubscribe", "list-id", "precedence", "auto-submitted", "x-auto-response-suppress")


def clean_addr(addr: str | None) -> str | None:
    addr = (addr or "").strip().strip("<>").lower()
    return addr if EMAIL_RE.match(addr) else None


def addr_list(values) -> list[tuple[str | None, str]]:
    out = []
    for name, addr in getaddresses([str(v) for v in values if v]):
        a = clean_addr(addr)
        if a:
            out.append((name.strip() or None, a))
    return out


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", raw)
    return html.unescape(re.sub(r"<[^>]+>", " ", raw))


def _body(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        text = part.get_content()
    except (LookupError, UnicodeDecodeError, AssertionError):
        return ""
    if part.get_content_type() == "text/html":
        text = html_to_text(text)
    return text[:MAX_BODY]


def to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def parse_rfc822(raw: bytes, direction_hint: str | None = None) -> RawMessage | None:
    msg = message_from_bytes(raw, policy=policy.default)
    name, addr = parseaddr(str(msg.get("From", "")))
    from_addr = clean_addr(addr)
    if not from_addr:
        return None
    try:
        date = to_utc(msg["Date"].datetime) if msg["Date"] else None
    except (ValueError, TypeError, AttributeError):
        date = None
    mid = str(msg.get("Message-ID", "")).strip().strip("<>") or None
    return RawMessage(
        message_id=mid,
        date=date,
        from_addr=from_addr,
        from_name=name.strip() or None,
        to=addr_list(msg.get_all("To", [])),
        cc=addr_list(msg.get_all("Cc", [])),
        subject=str(msg.get("Subject", "")),
        body_text=_body(msg),
        headers={h: str(msg[h]).lower() for h in KEEP_HEADERS if msg[h] is not None},
        direction_hint=direction_hint,
    )
