"""Coletas de empresas: acompanhamento de jobs, gravação do resultado do crawler e execução das três fontes."""
import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.compliance import create_compliance_record
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.jobs import finish_job, start_job
from app.core.models import Company, Contact, IcpProfile, JobRun, Workspace
from app.core.provenance import record_evidence
from app.hunter.companies import cnpj as cnpj_mod
from app.hunter.companies import places as places_mod
from app.hunter.companies.crawler import CrawlResult, crawl_site
from app.hunter.companies.fetch import SafeFetcher
from app.hunter.mailbox.noise import NoiseFilter, domain_of, dominio_tipo

FONTE_SITE = "crawler_site"
EXPECTED_ERRORS = (cnpj_mod.CnpjError, places_mod.PlacesError)


# ---------- acompanhamento ----------
def make_progress(job_id: int) -> Callable[..., None]:
    """Atualiza contadores/custo do job em outra sessão, para a tela mostrar o andamento em tempo real."""
    def report(contadores: dict, custo: float | None = None) -> None:
        with SessionLocal() as s:
            job = s.get(JobRun, job_id)
            if job is None:
                return
            job.contadores = {**(job.contadores or {}), **contadores}
            if custo is not None:
                job.custo_usd = custo
            s.commit()
    return report


def _mark_failed(job_id: int, message: str) -> None:
    with SessionLocal() as s:
        job = s.get(JobRun, job_id)
        if job:
            job.status = "falhou"
            job.contadores = {**(job.contadores or {}), "erro": message}
            job.finalizado_em = datetime.now(timezone.utc)
            s.commit()


def run_tracked(session: Session, ws: int, tipo: str, params: dict, work: Callable[[Callable], dict]) -> dict:
    job_id, created = start_job(session, tipo, params, ws)
    if not created:
        return {"job_id": job_id, "repetido": True}
    session.commit()  # o job precisa ficar visível para a tela de progresso
    progress = make_progress(job_id)
    try:
        counters = work(progress)
        finish_job(session, job_id, counters, counters.get("custo_usd", 0))
        session.commit()
    except EXPECTED_ERRORS as e:  # erro "esperado" (config/entrada): mostra a mensagem, não derruba o worker
        session.rollback()
        _mark_failed(job_id, str(e))
        return {"job_id": job_id, "repetido": False, "erro": str(e)}
    except Exception as e:
        session.rollback()
        _mark_failed(job_id, f"erro inesperado: {type(e).__name__}")
        raise
    return {"job_id": job_id, "repetido": False, **counters}


def icp_hash(icp: IcpProfile) -> str:
    data = {k: getattr(icp, k) for k in ("cnaes", "ufs", "municipios", "portes", "termos_maps")}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


# ---------- CNPJ ----------
def run_cnpj(session: Session, ws: int, icp: IcpProfile, folder: str | Path | None = None) -> dict:
    s = get_settings()
    folder = folder or s.cnpj_dir
    files = cnpj_mod.list_files(folder)
    params = {"icp": icp.id, "filtro": icp_hash(icp), "arquivos": cnpj_mod.files_signature(files), "limite": s.cnpj_max_empresas}
    return run_tracked(session, ws, "coleta_cnpj", params,
                       lambda progress: cnpj_mod.import_cnpj(session, ws, icp, folder, s.cnpj_max_empresas, progress))


# ---------- Google Places ----------
def run_places(session: Session, ws: int, icp: IcpProfile, client: places_mod.PlacesClient | None = None) -> dict:
    s = get_settings()
    params = {"icp": icp.id, "filtro": icp_hash(icp), "dia": datetime.now(timezone.utc).date().isoformat(),
              "limite_usd": s.places_max_usd_per_run}

    def work(progress):
        c = client or places_mod.PlacesClient(s.google_places_api_key)
        return places_mod.collect(session, ws, icp, c, s, progress)

    return run_tracked(session, ws, "coleta_places", params, work)


# ---------- sites ----------
def crawl_targets(session: Session, ws: int, icp: IcpProfile | None, limit: int) -> list[Company]:
    stmt = select(Company).where(Company.workspace_id == ws, Company.site.is_not(None), Company.site != "",
                                 Company.site_status == "nao_lido", Company.merged_into_id.is_(None))
    if icp is not None:
        if icp.ufs:
            stmt = stmt.where(Company.uf.in_([u.upper() for u in icp.ufs]))
        if icp.municipios:
            stmt = stmt.where(func.lower(Company.municipio).in_([m.lower() for m in icp.municipios]))
        prefixes = ["".join(ch for ch in c if ch.isdigit()) for c in icp.cnaes]
        if prefixes:
            digits = func.regexp_replace(Company.cnae_principal, r"\D", "", "g")
            stmt = stmt.where(or_(*[digits.startswith(p) for p in prefixes if p]))
    return list(session.scalars(stmt.order_by(Company.id).limit(limit)))


def apply_crawl(session: Session, ws: int, company: Company, res: CrawlResult, noise: NoiseFilter) -> dict:
    """Grava o que o crawler achou, sempre com evidência (fonte, página, confiança)."""
    counts = {"emails": 0, "contatos_novos": 0}
    company.site_status = res.status
    company.site_lido_em = datetime.now(timezone.utc)
    site_domain = company.dominio or ""
    first_url = f"https://{site_domain}/" if site_domain else None

    def ev(campo: str, valor: str, url: str | None, conf: float) -> None:
        record_evidence(session, workspace_id=ws, entity_type="company", entity_id=company.id, field=campo,
                        value=valor, source=FONTE_SITE, url=url or first_url, confidence=conf)

    for tel, url in res.telefones.items():
        ev("telefone", tel, url, 0.7)
        if not company.telefone:
            company.telefone = tel
    for num, url in res.whatsapps.items():
        ev("whatsapp", num, url, 0.8)
    for rede, url in res.redes.items():
        ev(rede, url, url, 0.8)
    if len(res.cnpjs) == 1:  # só aceita se o site cita um único CNPJ válido (rodapé pode citar parceiros)
        cnpj, url = next(iter(res.cnpjs.items()))
        ev("cnpj", cnpj, url, 0.7)
        if not company.cnpj and not session.scalar(select(Company.id).where(Company.workspace_id == ws,
                                                                           Company.cnpj == cnpj)):
            company.cnpj = cnpj

    for email, url in list(res.emails.items())[:10]:
        if noise.is_noise_address(email):
            continue
        counts["emails"] += 1
        edom = domain_of(email)
        same = bool(site_domain) and (edom == site_domain or edom.endswith("." + site_domain)
                                      or site_domain.endswith("." + edom))
        conf = 0.8 if same else 0.4  # email de outro domínio (gmail, contador) merece desconfiança
        contact = session.scalar(select(Contact).where(Contact.workspace_id == ws, Contact.email == email))
        if contact is None:
            contact = Contact(workspace_id=ws, company_id=company.id, email=email, dominio_tipo=dominio_tipo(email),
                              origem=f"site:{site_domain or 'site'}")
            session.add(contact)
            session.flush()
            counts["contatos_novos"] += 1
        elif contact.company_id is None:
            contact.company_id = company.id
        record_evidence(session, workspace_id=ws, entity_type="contact", entity_id=contact.id, field="email",
                        value=email, source=FONTE_SITE, url=url, confidence=conf)
        create_compliance_record(session, workspace_id=ws, entity_type="contact", entity_id=contact.id,
                                 origem=FONTE_SITE)
    create_compliance_record(session, workspace_id=ws, entity_type="company", entity_id=company.id, origem=FONTE_SITE)
    return counts


def run_crawl(session: Session, ws: int, icp: IcpProfile | None, limit: int = 200,
              fetcher: SafeFetcher | None = None) -> dict:
    s = get_settings()
    targets = crawl_targets(session, ws, icp, limit)
    ids = [c.id for c in targets]
    params = {"ids": hashlib.sha256(json.dumps(ids).encode()).hexdigest()[:16], "n": len(ids)}
    noise = NoiseFilter.from_config(session.get(Workspace, ws).config)

    def work(progress):
        f = fetcher or SafeFetcher(user_agent=s.crawl_user_agent, delay=s.crawl_delay_s, timeout=s.crawl_timeout_s)
        tot = {"sites": 0, "ok": 0, "bloqueados": 0, "precisa_js": 0, "inalcancaveis": 0, "emails": 0,
               "contatos_novos": 0}
        for i, company in enumerate(targets, start=1):
            try:
                res = crawl_site(f, company.site, s.crawl_max_pages)
            except Exception:  # noqa: BLE001 - um site quebrado não pode parar a coleta
                res = CrawlResult(status="erro")
            got = apply_crawl(session, ws, company, res, noise)
            tot["sites"] += 1
            tot["emails"] += got["emails"]
            tot["contatos_novos"] += got["contatos_novos"]
            key = {"ok": "ok", "precisa_js": "precisa_js", "inalcancavel": "inalcancaveis"}.get(res.status, "bloqueados")
            tot[key] += 1
            if i % 5 == 0:
                session.commit()
                progress({**tot, "total": len(targets)})
        return tot

    if not ids:
        return {"job_id": None, "repetido": False, "sites": 0, "aviso": "Nenhuma empresa com site ainda não lido."}
    return run_tracked(session, ws, "coleta_sites", params, work)
