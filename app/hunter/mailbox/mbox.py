"""Leitor de arquivos .mbox, .eml e pastas com .eml."""
import mailbox
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from app.hunter.mailbox.base import MailboxReader, RawMessage
from app.hunter.mailbox.parse import parse_rfc822


class MboxAdapter(MailboxReader):
    def __init__(self, path: str | Path, direction_hint: str | None = None):
        self.path = Path(path)
        self.direction_hint = direction_hint  # "enviado" para a pasta Enviados; None = decidir pelo remetente

    def _raw_messages(self) -> Iterator[bytes]:
        if self.path.is_dir():
            for f in sorted(self.path.glob("*.eml")):
                yield f.read_bytes()
        elif self.path.suffix.lower() == ".eml":
            yield self.path.read_bytes()
        else:
            box = mailbox.mbox(str(self.path), create=False)
            try:
                for key in box.iterkeys():
                    yield box.get_bytes(key)
            finally:
                box.close()

    def scan(self, since: datetime | None = None) -> Iterator[RawMessage]:
        for raw in self._raw_messages():
            msg = parse_rfc822(raw, self.direction_hint)
            if msg is None:
                continue
            if since and msg.date and msg.date < since:
                continue
            yield msg
