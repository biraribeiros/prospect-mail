"""F6 — trava do domínio principal: o domínio onde a empresa recebe e lê seus emails NUNCA faz prospecção fria.

O domínio principal é DERIVADO das caixas que o Hunter lê (e de qualquer domínio marcado 'principal'), a cada envio —
não depende de uma flag que alguém possa esquecer. Subdomínios do principal também são bloqueados. O tipo do domínio
não pode mudar depois do primeiro envio.
"""
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import get_history

from app.core.models import MailboxAccount, SenderDomain, Workspace
from app.core.security import decrypt_json


class SendBlocked(Exception):
    """Envio bloqueado por uma trava de segurança. A mensagem explica o motivo em português simples."""


class MainDomainError(SendBlocked):
    pass


class DomainNotReady(SendBlocked):
    pass


class SendingPaused(SendBlocked):
    pass


def norm_domain(d: str | None) -> str:
    return (d or "").strip().lower().rstrip(".").removeprefix("www.")


def same_org(a: str, b: str) -> bool:
    a, b = norm_domain(a), norm_domain(b)
    return bool(a and b) and (a == b or a.endswith("." + b) or b.endswith("." + a))


def domain_of_address(addr: str | None) -> str:
    return norm_domain((addr or "").rsplit("@", 1)[-1]) if addr and "@" in addr else ""


def main_domains(session: Session, ws: int) -> set[str]:
    """Domínios principais: das caixas lidas pelo Hunter (uso hunter/ambos), dos endereços cadastrados nelas e dos
    domínios marcados como 'principal'."""
    out: set[str] = set()
    for mb in session.scalars(select(MailboxAccount).where(MailboxAccount.workspace_id == ws,
                                                           MailboxAccount.uso.in_(("hunter", "ambos")))):
        out.add(norm_domain(mb.dominio))
        try:
            cfg = decrypt_json(mb.tokens_enc) if mb.tokens_enc else {}
        except Exception:  # noqa: BLE001
            cfg = {}
        for e in cfg.get("emails") or []:
            out.add(domain_of_address(e))
        for k in ("user", "email", "from"):
            out.add(domain_of_address(cfg.get(k)))
    for d in session.scalars(select(SenderDomain.nome).where(SenderDomain.workspace_id == ws, SenderDomain.tipo == "principal")):
        out.add(norm_domain(d))
    return {d for d in out if d}


def is_main_domain(session: Session, ws: int, domain: str) -> bool:
    return any(same_org(domain, m) for m in main_domains(session, ws))


def register_sender_domain(session: Session, ws: int, nome: str, tipo: str = "secundario", selector: str | None = None) -> SenderDomain:
    nome = norm_domain(nome)
    if not nome or "." not in nome:
        raise ValueError("Informe um domínio válido (ex.: contato-agencia.com.br).")
    if tipo == "secundario" and is_main_domain(session, ws, nome):
        raise MainDomainError(f"{nome} é (ou pertence ao) domínio principal lido pelo Hunter: não pode ser usado para prospecção.")
    d = session.scalar(select(SenderDomain).where(SenderDomain.workspace_id == ws, SenderDomain.nome == nome))
    if d is None:
        d = SenderDomain(workspace_id=ws, nome=nome, tipo=tipo, dkim_selector=selector)
        session.add(d)
    elif d.tipo != tipo:
        d.tipo = tipo  # o evento abaixo barra a troca se já houve envio
    session.flush()
    return d


def assert_can_send(session: Session, mailbox: MailboxAccount) -> SenderDomain:
    """Todas as travas, na ordem. Levanta SendBlocked (ou subclasse) com o motivo. Chamado a CADA envio."""
    ws = mailbox.workspace_id
    wsrow = session.get(Workspace, ws)
    if (wsrow.config or {}).get("envio_pausado"):
        raise SendingPaused("O envio está pausado pelo disjuntor de segurança. Um administrador precisa liberar.")
    if mailbox.uso not in ("envio", "ambos") or mailbox.tipo in ("mbox",):
        raise SendBlocked("Esta caixa não está cadastrada para envio.")
    if mailbox.pausada:
        raise SendBlocked("Esta caixa está pausada.")
    dom = session.get(SenderDomain, mailbox.sender_domain_id) if mailbox.sender_domain_id else None
    if dom is None:
        raise DomainNotReady("A caixa de envio não tem domínio cadastrado.")
    if dom.tipo != "secundario" or is_main_domain(session, ws, dom.nome):
        raise MainDomainError(f"{dom.nome} é o domínio principal: prospecção fria só sai de domínios secundários.")
    if not (dom.spf_ok and dom.dkim_ok and dom.dmarc_ok and dom.rdns_ok):
        faltam = [n for n, v in (("SPF", dom.spf_ok), ("DKIM", dom.dkim_ok), ("DMARC", dom.dmarc_ok), ("DNS reverso", dom.rdns_ok)) if not v]
        raise DomainNotReady(f"{dom.nome} ainda não passou na checagem de DNS ({', '.join(faltam)}).")
    return dom


# O tipo do domínio é imutável depois do primeiro envio (vale para qualquer caminho de código que use o ORM).
@event.listens_for(SenderDomain, "before_update")
def _tipo_imutavel(mapper, connection, target: SenderDomain) -> None:
    hist = get_history(target, "tipo")
    if hist.has_changes() and hist.deleted and target.ja_enviou:
        raise MainDomainError("O tipo do domínio não pode mudar depois do primeiro envio.")
