"""Regras 6 e 11: jobs idempotentes (hash dos parâmetros) com custo registrado."""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import JobRun


def params_hash(params: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()


def start_job(
    session: Session, tipo: str, parametros: dict[str, Any], workspace_id: int | None = None
) -> tuple[int, bool]:
    """Retorna (job_run_id, criado). Se já existe job igual (não falho), devolve o existente com criado=False."""
    h = params_hash(parametros)
    existing = session.scalar(
        select(JobRun).where(
            JobRun.tipo == tipo,
            JobRun.parametros_hash == h,
            JobRun.workspace_id.is_(None) if workspace_id is None else JobRun.workspace_id == workspace_id,
            JobRun.status != "falhou",
        )
    )
    if existing:
        return existing.id, False
    job = JobRun(workspace_id=workspace_id, tipo=tipo, parametros_hash=h, parametros=parametros)
    session.add(job)
    session.flush()
    return job.id, True


def finish_job(
    session: Session,
    job_id: int,
    contadores: dict[str, Any],
    custo_usd: float | Decimal = 0,
    status: str = "concluido",
) -> JobRun:
    job = session.get(JobRun, job_id)
    if job is None:
        raise ValueError(f"job_run {job_id} não existe")
    job.contadores = contadores
    job.custo_usd = Decimal(str(custo_usd))
    job.status = status
    job.finalizado_em = datetime.now(timezone.utc)
    session.flush()
    return job
