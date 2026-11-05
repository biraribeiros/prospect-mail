"""Telas do Filtro: validação de emails (Etapa 4) e enriquecimento/notas (Etapa 5a)."""
import time

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.models import Company, Contact, JobRun, Verification
from app.filtro.validation.service import distribution
from app.filtro.validation.verifiers import SMTP_NOTE, build_verifier, port25_open
from app.web.deps import Ctx, current_ctx, editor_ctx, flash, render
from app.web.routes.empresas import NOMES, _queue

router = APIRouter(prefix="/filtro")
PAGE = 50
_PORT25 = {"t": 0.0, "v": False}


def _port25() -> bool:
    if time.time() - _PORT25["t"] > 600:  # a checagem abre uma conexão: no máximo a cada 10 minutos
        _PORT25.update(t=time.time(), v=port25_open())
    return _PORT25["v"]


def _jobs(ctx: Ctx, tipos: tuple[str, ...], limit: int = 8):
    return list(ctx.db.scalars(select(JobRun).where(JobRun.workspace_id == ctx.workspace_id, JobRun.tipo.in_(tipos))
                               .order_by(JobRun.id.desc()).limit(limit)))


@router.get("")
def filtro_home(request: Request, status: str = "", pagina: int = 1, ctx: Ctx = Depends(current_ctx)):
    s = get_settings()
    port25 = _port25() if s.verifier_provider == "reacher" else None
    _, aviso = build_verifier(s.verifier_provider, s.verifier_api_key, s.reacher_url, port25=port25)
    dist = distribution(ctx.db, ctx.workspace_id)
    conds = [Contact.workspace_id == ctx.workspace_id, Contact.email.is_not(None)]
    if status:
        conds.append(Contact.email_status == status)
    pagina = max(1, pagina)
    rows = ctx.db.execute(select(Contact, Company).outerjoin(Company, Company.id == Contact.company_id).where(*conds)
                          .order_by(Contact.nota_final.desc().nullslast(), Contact.id.desc()).limit(PAGE)
                          .offset((pagina - 1) * PAGE)).all()
    motivos = {}
    for cid, det in ctx.db.execute(select(Verification.contact_id, Verification.detalhes).where(
            Verification.contact_id.in_([c.id for c, _ in rows] or [0])).order_by(Verification.id)):
        motivos[cid] = (det or {}).get("motivo", "")
    return render(request, "filtro_home.html", ctx, ativo="filtro", dist=dist, rows=rows, filtro=status, aviso=aviso,
                  provedor=s.verifier_provider, smtp_note=SMTP_NOTE, motivos=motivos, pagina=pagina,
                  jobs=_jobs(ctx, ("validacao_emails", "enriquecimento")), nomes=NOMES, dias=s.verifier_cache_days)


@router.post("/validar")
async def validar(request: Request, limite: int = Form(500), refazer: str = Form(""), ctx: Ctx = Depends(editor_ctx)):
    await _queue(request, "validate_emails_job", ctx.workspace_id, max(1, min(limite, 5000)), refazer == "sim",
                 ok="Validação iniciada. Acompanhe abaixo.")
    return RedirectResponse("/filtro", status_code=303)


@router.post("/enriquecer")
async def enriquecer(request: Request, ctx: Ctx = Depends(editor_ctx)):
    await _queue(request, "enrich_job", ctx.workspace_id, ok="Enriquecimento iniciado. Acompanhe abaixo.")
    return RedirectResponse("/filtro", status_code=303)


__all__ = ["router", "flash", "func"]
