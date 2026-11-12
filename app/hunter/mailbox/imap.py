"""Leitor IMAP genérico (Locaweb, HostGator, Horde etc.). SSL ou STARTTLS. Abre as pastas só para leitura."""
import imaplib
import re
import ssl
from collections.abc import Callable, Iterator
from datetime import datetime

from app.hunter.mailbox.base import MailboxReader, RawMessage
from app.hunter.mailbox.parse import parse_rfc822

MESES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SENT_NAMES = {"sent", "sent items", "sent messages", "enviados", "itens enviados", "mensagens enviadas",
              "inbox.sent", "inbox.enviados"}
LIST_RE = re.compile(r'\((?P<flags>[^)]*)\)\s+(?:"(?P<sep>[^"]*)"|NIL)\s+(?P<name>.+)$')
CHUNK = 50


def imap_date(d: datetime) -> str:
    return f"{d.day:02d}-{MESES[d.month - 1]}-{d.year}"


class ImapAdapter(MailboxReader):
    def __init__(self, host: str, port: int, user: str, password: str, security: str = "ssl",
                 connection_factory: Callable[[], "imaplib.IMAP4"] | None = None):
        self.host, self.port, self.user, self.password, self.security = host, port, user, password, security
        self._factory = connection_factory

    def _connect(self):
        if self._factory:
            return self._factory()
        if self.security == "ssl":
            conn = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=ssl.create_default_context(), timeout=30)
        else:
            conn = imaplib.IMAP4(self.host, self.port, timeout=30)
            if self.security == "starttls":
                conn.starttls(ssl.create_default_context())
        conn.login(self.user, self.password)
        return conn

    def test_connection(self) -> None:
        conn = self._connect()
        try:
            conn.noop()
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001 - encerrando; erro aqui não importa
                pass

    @staticmethod
    def _folders(conn) -> list[tuple[str, str | None]]:
        """[(nome, direction_hint)] — INBOX (decide pelo remetente) + pastas de Enviados."""
        out: list[tuple[str, str | None]] = [("INBOX", None)]
        typ, data = conn.list()
        if typ != "OK":
            return out
        for line in data or []:
            m = LIST_RE.match(line.decode("utf-8", "replace") if isinstance(line, bytes) else str(line))
            if not m:
                continue
            name = m.group("name").strip().strip('"')
            flags = m.group("flags").lower()
            if "\\all" in flags:  # Gmail: "Todos os e-mails" já contém recebidos, enviados e arquivados (sem spam/lixeira)
                return [(name, None)]
            if "\\sent" in flags or name.lower() in SENT_NAMES:
                out.append((name, "enviado"))
        return out

    def scan(self, since: datetime | None = None) -> Iterator[RawMessage]:
        conn = self._connect()
        try:
            for folder, hint in self._folders(conn):
                typ, _ = conn.select(f'"{folder}"', readonly=True)
                if typ != "OK":
                    continue
                typ, data = conn.search(None, "SINCE", imap_date(since)) if since else conn.search(None, "ALL")
                if typ != "OK" or not data or not data[0]:
                    continue
                ids = data[0].split()
                for i in range(0, len(ids), CHUNK):
                    typ, parts = conn.fetch(b",".join(ids[i:i + CHUNK]).decode(), "(BODY.PEEK[])")
                    if typ != "OK":
                        continue
                    for part in parts or []:
                        if isinstance(part, tuple) and len(part) > 1:
                            msg = parse_rfc822(part[1], hint)
                            if msg and not (since and msg.date and msg.date < since):
                                yield msg
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass
