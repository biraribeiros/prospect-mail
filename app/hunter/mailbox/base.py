"""Interface única dos leitores de caixa (regra 4: toda fonte externa é um adaptador plugável)."""
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawMessage:
    """Mensagem lida de uma caixa. `subject` e `body_text` existem só em memória durante a varredura:
    servem para achar a assinatura e as palavras-chave e NUNCA são gravados no banco."""

    message_id: str | None
    date: datetime | None  # sempre com fuso (UTC)
    from_addr: str
    from_name: str | None = None
    to: list[tuple[str | None, str]] = field(default_factory=list)
    cc: list[tuple[str | None, str]] = field(default_factory=list)
    subject: str = ""
    body_text: str = ""
    headers: dict[str, str] = field(default_factory=dict)  # só os usados pelo filtro de ruído
    direction_hint: str | None = None  # "enviado" | "recebido" | None (decidir pelo remetente)


class MailboxReader(ABC):
    @abstractmethod
    def scan(self, since: datetime | None = None) -> Iterator[RawMessage]:
        """Lê as mensagens (Recebidos e Enviados) a partir de `since`; None = tudo."""

    def cursor(self) -> dict:
        """Estado para varredura incremental (ex.: historyId do Gmail); vazio por padrão."""
        return {}
