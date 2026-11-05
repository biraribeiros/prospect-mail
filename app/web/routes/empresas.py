import json
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import exists, func, select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.models import Company, Contact, Evidence, IcpProfile, JobRun
from app.hunter.companies import cnpj as cnpj_mod
from app.hunter.companies import places as places_mod
from app.web.deps import Ctx, current_ctx, editor_ctx, flash, render
from app.workers.queue import enqueue

router = APIRouter(prefix="/hunter")
PAGE = 50
TIPOS = ("coleta_cnpj", "coleta_places", "coleta_sites")
NOMES = {"coleta_cnpj": "Base CNPJ", "coleta_places": "Google Places", "coleta_sites": "Leitura de sites"}


def _icps(ctx: Ctx) -> list[IcpProfile]:
    return list(ctx.db.scalars(select(IcpProfile).where(IcpProfile.workspace_id == ctx.workspace_id)
                               .order_by(IcpProfile.nome)))


def _icp(ctx: Ctx, icp_id: int | None) -> IcpProfile | None:
    if not icp_id:
        return None
    icp = ctx.db.get(IcpProfile, icp_id)
    if icp is None or icp.workspace_id != ctx.workspace_id:  # isolamento entre workspaces
        raise HTTPException(status_code=404)
    return icp


def _recent_jobs(ctx: Ctx, limit: int = 10) -> list[JobRun]:
    return list(ctx.db.scalars(select(JobRun).where(JobRun.workspace_id == ctx.workspace_id, JobRun.tipo.in_(TIPOS))
                               .order_by(JobRun.id.desc()).limit(limit)))


async def _queue(request: Request, job: str, *args, ok: str) -> None:
    try:
        await enqueue(job, *args)
        flash(request, ok)
    except Exception:  # noqa: BLE001
        flash(request, "A fila de tarefas está indisponível agora. Tente novamente em instantes.", "err")


# ---------- empresas ----------
def _company_filters(q: str, uf: str, fonte: str, site: str, cnpj: str):
    conds = []
    if q.strip():
        t = q.strip()
        conds.append(Company.nome_fantasia.icontains(t, autoescape=True) | Company.razao_social.icontains(t, autoescape=True)
                     | Company.dominio.icontains(t, autoescape=True) | Company.cnpj.icontains(t.replace(".", "").replace("/", "").replace("-", ""), autoescape=True))
    if uf:
        conds.append(Company.uf == uf.upper())
    if fonte:
        conds.append(exists().where(Evidence.entidade_tipo == "company", Evidence.entidade_id == Company.id,
                                    Evidence.fonte == fonte))
    if site == "com":
        conds.append(Company.site.is_not(None))
    elif site == "sem":
        conds.append(Company.site.is_(None))
    elif site == "nao_lido":
        conds.append(Company.site.is_not(None) & (Company.site_status == "nao_lido"))
    if cnpj == "com":
        conds.append(Company.cnpj.is_not(None))
    elif cnpj == "sem":
        conds.append(Company.cnpj.is_(None))
    return conds


@router.get("/empresas")
def empresas(request: Request, q: str = "", uf: str = "", fonte: str = "", site: str = "", cnpj: str = "",
             pagina: int = 1, ctx: Ctx = Depends(current_ctx)):
    base = select(Company).where(Company.workspace_id == ctx.workspace_id, *_company_filters(q, uf, fonte, site, cnpj))
    total = ctx.db.scalar(select(func.count()).select_from(base.subquery()))
    pagina = max(1, pagina)
    rows = ctx.db.scalars(base.order_by(Company.id.desc()).limit(PAGE).offset((pagina - 1) * PAGE)).all()
    ids = [c.id for c in rows]
    fontes: dict[int, list[str]] = {}
    for cid, src in ctx.db.execute(select(Evidence.entidade_id, Evidence.fonte).where(
            Evidence.workspace_id == ctx.workspace_id, Evidence.entidade_tipo == "company", Evidence.entidade_id.in_(ids)
    ).distinct()):
        fontes.setdefault(cid, []).append(src)
    contatos = dict(ctx.db.execute(select(Contact.company_id, func.count()).where(
        Contact.workspace_id == ctx.workspace_id, Contact.company_id.in_(ids)).group_by(Contact.company_id)).all())
    keep = {k: v for k, v in {"q": q, "uf": uf, "fonte": fonte, "site": site, "cnpj": cnpj}.items() if v}
    return render(request, "hunter_empresas.html", ctx, ativo="hunter", rows=rows, total=total, pagina=pagina,
                  paginas=max(1, -(-total // PAGE)), fontes=fontes, contatos=contatos,
                  f={"q": q, "uf": uf, "fonte": fonte, "site": site, "cnpj": cnpj}, qs=urlencode(keep))


@router.get("/empresas/{company_id:int}")
def empresa(company_id: int, request: Request, ctx: Ctx = Depends(current_ctx)):
    c = ctx.db.get(Company, company_id)
    if c is None or c.workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=404)
    ev = ctx.db.scalars(select(Evidence).where(Evidence.workspace_id == ctx.workspace_id,
                                               Evidence.entidade_tipo == "company", Evidence.entidade_id == c.id)
                        .order_by(Evidence.campo, Evidence.id)).all()
    contatos = ctx.db.scalars(select(Contact).where(Contact.workspace_id == ctx.workspace_id,
                                                    Contact.company_id == c.id).order_by(Contact.id)).all()
    return render(request, "hunter_empresa.html", ctx, ativo="hunter", c=c, ev=ev, contatos=contatos)


# ---------- coleta ----------
def _coleta_page(request, ctx, status=200, **extra):
    s = get_settings()
    files = cnpj_mod.list_files(s.cnpj_dir)
    arquivos = [(k, [f.name for f in v]) for k, v in files.items() if v]
    return render(request, "hunter_coleta.html", ctx, status=status, ativo="hunter", icps=_icps(ctx),
                  jobs=_recent_jobs(ctx), nomes=NOMES, arquivos=arquivos, cnpj_dir=s.cnpj_dir,
                  tem_chave=bool(s.google_places_api_key), limite_usd=s.places_max_usd_per_run, **extra)


@router.get("/coleta")
def coleta(request: Request, ctx: Ctx = Depends(current_ctx)):
    return _coleta_page(request, ctx)


@router.get("/coleta/recentes")
def coleta_recentes(request: Request, ctx: Ctx = Depends(current_ctx)):
    return render(request, "_jobs_table.html", ctx, jobs=_recent_jobs(ctx), nomes=NOMES)


@router.post("/coleta/cnpj")
async def coleta_cnpj(request: Request, icp_id: int = Form(...), ctx: Ctx = Depends(editor_ctx)):
    icp = _icp(ctx, icp_id)
    await _queue(request, "collect_cnpj_job", ctx.workspace_id, icp.id,
                 ok="Importação da base CNPJ iniciada. Acompanhe abaixo.")
    return RedirectResponse("/hunter/coleta", status_code=303)


@router.post("/coleta/places/estimar")
def places_estimar(request: Request, icp_id: int = Form(...), ctx: Ctx = Depends(editor_ctx)):
    icp = _icp(ctx, icp_id)
    try:
        est = places_mod.estimate(ctx.db, ctx.workspace_id, icp, get_settings())
    except places_mod.PlacesError as e:
        flash(request, str(e), "err")
        return RedirectResponse("/hunter/coleta", status_code=303)
    return _coleta_page(request, ctx, estimativa=est, estimativa_icp=icp)


@router.post("/coleta/places/rodar")
async def places_rodar(request: Request, icp_id: int = Form(...), confirmo: str = Form(""),
                       ctx: Ctx = Depends(editor_ctx)):
    icp = _icp(ctx, icp_id)
    if confirmo != "sim":
        flash(request, "Confirme o custo estimado para iniciar a busca.", "err")
        return RedirectResponse("/hunter/coleta", status_code=303)
    if not get_settings().google_places_api_key:
        flash(request, "Chave do Google Places não configurada (GOOGLE_PLACES_API_KEY no arquivo .env).", "err")
        return RedirectResponse("/hunter/coleta", status_code=303)
    await _queue(request, "collect_places_job", ctx.workspace_id, icp.id,
                 ok="Busca no Google Places iniciada. Acompanhe abaixo o progresso e o custo.")
    return RedirectResponse("/hunter/coleta", status_code=303)


@router.post("/coleta/sites")
async def coleta_sites(request: Request, icp_id: str = Form(""), limite: int = Form(200),
                       ctx: Ctx = Depends(editor_ctx)):
    icp = _icp(ctx, int(icp_id)) if icp_id.strip().isdigit() else None
    await _queue(request, "crawl_sites_job", ctx.workspace_id, icp.id if icp else None, max(1, min(limite, 2000)),
                 ok="Leitura de sites iniciada. Acompanhe abaixo.")
    return RedirectResponse("/hunter/coleta", status_code=303)


def _get_job(ctx: Ctx, job_id: int) -> JobRun:
    job = ctx.db.get(JobRun, job_id)
    if job is None or job.workspace_id != ctx.workspace_id or job.tipo not in TIPOS:
        raise HTTPException(status_code=404)
    return job


@router.get("/coleta/{job_id:int}")
def coleta_job(job_id: int, request: Request, ctx: Ctx = Depends(current_ctx)):
    job = _get_job(ctx, job_id)
    return render(request, "hunter_coleta_job.html", ctx, ativo="hunter", job=job, nome=NOMES[job.tipo])


@router.get("/coleta/{job_id:int}/eventos")
def coleta_eventos(job_id: int, ctx: Ctx = Depends(current_ctx)):
    """Progresso em tempo real (Server-Sent Events): estado do job a cada mudança, até terminar."""
    _get_job(ctx, job_id)

    def stream():
        last = None
        while True:
            with SessionLocal() as s:
                job = s.get(JobRun, job_id)
                data = {"status": job.status, "contadores": job.contadores or {}, "custo_usd": float(job.custo_usd or 0)}
            payload = json.dumps(data)
            if payload != last:
                last = payload
                yield f"data: {payload}\n\n"
            else:
                yield ": ping\n\n"  # mantém a conexão e detecta quem fechou a página
            if data["status"] != "em_andamento":
                return
            time.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
