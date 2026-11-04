"""Regra 5: todo dado persistido registra fonte, URL, confiança (0-1) e data de coleta."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import Evidence


def record_evidence(
    session: Session,
    *,
    workspace_id: int,
    entity_type: str,
    entity_id: int,
    field: str,
    value: str | None,
    source: str,
    url: str | None = None,
    confidence: float = 0.5,
    dedupe: bool = True,
) -> Evidence:
    if not 0 <= confidence <= 1:
        raise ValueError("confidence deve estar entre 0 e 1")
    if not source:
        raise ValueError("source é obrigatório")
    if dedupe:  # regra 6: repetir a mesma coleta não duplica
        existing = session.scalar(
            select(Evidence).where(
                Evidence.workspace_id == workspace_id,
                Evidence.entidade_tipo == entity_type,
                Evidence.entidade_id == entity_id,
                Evidence.campo == field,
                Evidence.valor == value,
                Evidence.fonte == source,
            )
        )
        if existing:
            return existing
    ev = Evidence(
        workspace_id=workspace_id,
        entidade_tipo=entity_type,
        entidade_id=entity_id,
        campo=field,
        valor=value,
        fonte=source,
        url=url,
        confianca=confidence,
    )
    session.add(ev)
    session.flush()
    return ev
