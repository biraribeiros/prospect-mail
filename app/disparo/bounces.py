"""Etapa 6b: bounces. Hard → email inválido + supressão. Soft → 2 tentativas; depois o contato fica 'arriscado'."""
import re
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.message import Message

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import Contact, OutboundMessage, Suppression
from app.disparo import breaker

SOFT_MAX = 2


def parse_dsn(raw: bytes) -> tuple[str | None, str | None]:
    """Lê um relatório de falha de entrega (DSN, RFC 3464). Devolve (message_id_original, 'hard'|'soft'|None)."""
    msg: Message = message_from_bytes(raw)
    kind = None
    original = None
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype == "message/delivery-status":
            for block in part.get_payload():
                st = str(block.get("Status", "")).strip()
                action = str(block.get("Action", "")).strip().lower()
                if st.startswith("5") or (action == "failed" and not st.startswith("4")):
                    kind = "hard"
                elif st.startswith("4") or action == "delayed":
                    kind = kind or "soft"
        elif ctype in ("message/rfc822", "text/rfc822-headers"):
            inner = part.get_payload()
            inner = inner[0] if isinstance(inner, list) and inner else inner
            if isinstance(inner, Message):
                original = (inner.get("Message-ID") or "").strip() or original
            elif isinstance(inner, str):
                m = re.search(r"^Message-ID:\s*(<[^>]+>)", inner, re.I | re.M)
                original = m.group(1) if m else original
    return original, kind


def register_bounce(session: Session, ws: int, message_id: str, kind: str, now: datetime | None = None) -> str:
    """Aplica o bounce à mensagem. Retorna o que foi feito: 'invalido' | 'reagendada' | 'arriscado' | 'ignorado'."""
    now = now or datetime.now(timezone.utc)
    msg = session.scalar(select(OutboundMessage).where(OutboundMessage.workspace_id == ws, OutboundMessage.message_id == message_id))
    if msg is None or kind not in ("hard", "soft"):
        return "ignorado"
    ct = session.get(Contact, msg.contact_id)
    msg.bounce_tipo, msg.bounce_em = kind, now
    if kind == "hard":
        ct.email_status = "invalido"
        if not session.scalar(select(Suppression.id).where(Suppression.workspace_id == ws, Suppression.email_ou_dominio == ct.email.lower())):
            session.add(Suppression(workspace_id=ws, email_ou_dominio=ct.email.lower(), motivo="bounce permanente", origem="bounce"))
        for pend in session.scalars(select(OutboundMessage).where(OutboundMessage.contact_id == ct.id,
                                                                  OutboundMessage.status.in_(("rascunho", "aprovada", "agendada")))):
            pend.status, pend.motivo = "cancelada", "email inválido (bounce permanente)"
        res = "invalido"
    else:
        soft_count = len(list(session.scalars(select(OutboundMessage.id).where(OutboundMessage.contact_id == ct.id,
                                                                               OutboundMessage.bounce_tipo == "soft"))))
        if soft_count < SOFT_MAX:  # 1º bounce temporário: uma nova tentativa; 2º: vira 'arriscado'
            retry = OutboundMessage(workspace_id=ws, contact_id=ct.id, company_id=msg.company_id, hypothesis_id=msg.hypothesis_id,
                                    icp_id=msg.icp_id, thread_raiz_id=msg.thread_raiz_id, passo=msg.passo, objetivo=msg.objetivo,
                                    variante=msg.variante, assunto=msg.assunto, corpo=msg.corpo, status="aprovada",
                                    agendada_para=now + timedelta(hours=6), motivo="nova tentativa após bounce temporário")
            session.add(retry)
            res = "reagendada"
        else:
            ct.email_status = "arriscado"
            res = "arriscado"
    session.flush()
    breaker.evaluate(session, ws, now)
    return res


def ingest_dsn(session: Session, ws: int, raw: bytes) -> str:
    mid, kind = parse_dsn(raw)
    if not mid or not kind:
        return "ignorado"
    return register_bounce(session, ws, mid, kind)
