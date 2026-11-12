"""Etapa 2b: busca sinais comerciais por empresa e grava só o que tem fonte verificável (url http/https)."""
import hashlib
import json
from datetime import datetime, timezone

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.models import Company, IcpProfile, OpportunitySignal
from app.hunter.companies.fetch import SafeFetcher
from app.hunter.companies.service import run_tracked
from app.hunter.signals.sources import (CareersPageJobSource, GoogleNewsSource, JobSource, NewsSource, RawSignal,
                                        RssJobSource)


def save_signal(session: Session, ws: int, company: Company, raw: RawSignal) -> bool:
    """Grava o sinal. Retorna True se é novo. Sem fonte/URL verificável: não grava."""
    if not raw.fonte or not raw.url.lower().startswith(("http://", "https://")):
        return False
    h = hashlib.sha1(f"{raw.tipo}|{raw.url}|{raw.titulo}".encode()).hexdigest()[:20]
    exists = session.scalar(select(OpportunitySignal.id).where(
        OpportunitySignal.workspace_id == ws, OpportunitySignal.company_id == company.id,
        OpportunitySignal.tipo == raw.tipo, OpportunitySignal.hash == h))
    if exists:
        return False
    session.add(OpportunitySignal(workspace_id=ws, company_id=company.id, tipo=raw.tipo, titulo=raw.titulo, fonte=raw.fonte,
                                  url=raw.url, confianca=raw.confianca, polaridade=raw.polaridade, hash=h,
                                  valido_ate=raw.valido_ate))
    session.flush()
    return True


def signal_targets(session: Session, ws: int, icp: IcpProfile | None, limit: int) -> list[Company]:
    """Empresas do perfil com nome (site não é obrigatório: notícias funcionam só com o nome)."""
    stmt = select(Company).where(Company.workspace_id == ws, Company.merged_into_id.is_(None))
    if icp is not None:
        if icp.ufs:
            stmt = stmt.where(Company.uf.in_([u.upper() for u in icp.ufs]))
        if icp.municipios:
            stmt = stmt.where(func.lower(Company.municipio).in_([m.lower() for m in icp.municipios]))
        prefixes = ["".join(ch for ch in c if ch.isdigit()) for c in icp.cnaes]
        if any(prefixes):
            digits = func.regexp_replace(Company.cnae_principal, r"\D", "", "g")
            stmt = stmt.where(or_(*[digits.startswith(p) for p in prefixes if p]))
    stmt = stmt.where((Company.nome_fantasia.is_not(None)) | (Company.razao_social.is_not(None)))
    return list(session.scalars(stmt.order_by(Company.id).limit(limit)))


def names_of(c: Company) -> list[str]:
    return [n for n in (c.nome_fantasia, c.razao_social) if n]


def run_signals(session: Session, ws: int, icp: IcpProfile | None, limit: int = 200,
                jobs: list[JobSource] | None = None, news: NewsSource | None = None,
                fetcher: SafeFetcher | None = None, http: httpx.Client | None = None) -> dict:
    s = get_settings()
    targets = signal_targets(session, ws, icp, limit)
    ids = [c.id for c in targets]
    params = {"ids": hashlib.sha256(json.dumps(ids).encode()).hexdigest()[:16], "n": len(ids),
              "dia": datetime.now(timezone.utc).date().isoformat()}
    if not ids:
        return {"job_id": None, "repetido": False, "empresas": 0, "novos": 0, "aviso": "Nenhuma empresa para analisar."}

    def work(progress):
        client = http or httpx.Client(timeout=15, follow_redirects=False)
        sources = jobs if jobs is not None else [
            CareersPageJobSource(fetcher or SafeFetcher(user_agent=s.crawl_user_agent, delay=s.crawl_delay_s,
                                                       timeout=s.crawl_timeout_s))]
        templates = [u.strip() for u in s.signals_job_rss_urls.split(",") if u.strip()]
        if jobs is None and templates:
            sources.append(RssJobSource(client, templates))
        news_src = news if news is not None else GoogleNewsSource(client)
        tot = {"empresas": 0, "novos": 0, "vagas": 0, "noticias": 0, "custo_usd": 0}
        for i, c in enumerate(targets, start=1):
            nomes = names_of(c)
            found: list[RawSignal] = []
            for src in sources:
                try:
                    found += src.find(nomes[0], c.site)
                except Exception:  # noqa: BLE001 - uma fonte quebrada não derruba as demais
                    continue
            try:
                found += news_src.find(nomes[0], nomes[1:])
            except Exception:  # noqa: BLE001
                pass
            for raw in found:
                if save_signal(session, ws, c, raw):
                    tot["novos"] += 1
                    tot["vagas" if raw.tipo == "vaga" else "noticias"] += 1
            tot["empresas"] += 1
            if i % 5 == 0:
                session.commit()
                progress({**tot, "total": len(targets)})
        return tot

    return run_tracked(session, ws, "coleta_sinais", params, work)
