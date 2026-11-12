"""Descadastro: supressão imediata + registro de conformidade + cancelamento de tudo que estava na fila para o contato."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.models import ComplianceRecord, Contact, OutboundMessage, Suppression


def suppress(session: Session, ws: int, email: str, motivo: str = "descadastro pelo destinatário", origem: str = "descadastro") -> int:
    """Suprime o email no workspace. Devolve quantos contatos foram afetados."""
    email = email.strip().lower()
    if not session.scalar(select(Suppression.id).where(Suppression.workspace_id == ws, Suppression.email_ou_dominio == email)):
        session.add(Suppression(workspace_id=ws, email_ou_dominio=email, motivo=motivo, origem=origem))
    n = 0
    now = datetime.now(timezone.utc)
    for ct in session.scalars(select(Contact).where(Contact.workspace_id == ws, Contact.email == email)):
        ct.bloqueado_envio, ct.bloqueio_motivo = True, "descadastro"
        n += 1
        for m in session.scalars(select(OutboundMessage).where(OutboundMessage.contact_id == ct.id,
                                                               OutboundMessage.status.in_(("rascunho", "aprovada", "agendada")))):
            m.status, m.motivo = "cancelada", "destinatário pediu para sair"
        for rec in session.scalars(select(ComplianceRecord).where(ComplianceRecord.workspace_id == ws,
                                                                  ComplianceRecord.entidade_tipo == "contact",
                                                                  ComplianceRecord.entidade_id == ct.id)):
            rec.historico_oposicao = [*(rec.historico_oposicao or []), {"em": now.isoformat(), "origem": origem, "motivo": motivo}]
            flag_modified(rec, "historico_oposicao")
    session.flush()
    return n
