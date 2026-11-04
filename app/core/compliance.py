"""Regra 12: toda abordagem registra origem, finalidade, base legal e data de coleta."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.models import ComplianceRecord

FINALIDADE_PADRAO = "prospeccao_b2b"
# LGPD adiada até a comercialização (decisão do dono, 04/11/2026): a base legal fica marcada como "a avaliar".
BASE_LEGAL_PADRAO = "legitimo_interesse_a_avaliar"


def create_compliance_record(
    session: Session,
    *,
    workspace_id: int,
    entity_type: str,
    entity_id: int,
    origem: str,
    finalidade: str = FINALIDADE_PADRAO,
    base_legal: str = BASE_LEGAL_PADRAO,
    coletado_em: datetime | None = None,
) -> ComplianceRecord:
    existing = session.scalar(
        select(ComplianceRecord).where(
            ComplianceRecord.entidade_tipo == entity_type,
            ComplianceRecord.entidade_id == entity_id,
            ComplianceRecord.origem_dado == origem,
        )
    )
    if existing:
        return existing
    when = coletado_em or datetime.now(timezone.utc)
    rec = ComplianceRecord(
        workspace_id=workspace_id,
        entidade_tipo=entity_type,
        entidade_id=entity_id,
        origem_dado=origem,
        finalidade=finalidade,
        base_legal=base_legal,
        coletado_em=when,
        retencao_ate=when + timedelta(days=get_settings().retention_days),
    )
    session.add(rec)
    session.flush()
    return rec
