"""Jobs do worker. Cada job abre a própria sessão e confirma (commit) só no final."""
from datetime import datetime, timedelta, timezone

from app.core.db import SessionLocal
from app.core.models import MailboxAccount, Workspace
from app.hunter.mailbox.factory import build_llm, build_reader, own_addresses
from app.hunter.mailbox.noise import NoiseFilter
from app.hunter.mailbox.service import OVERLAP_DIAS, file_sha, scan_mailbox
from app.core.security import decrypt_json


def run_scan(session, mailbox: MailboxAccount):
    """Varre uma caixa e devolve o ScanResult (sem commit)."""
    ws = session.get(Workspace, mailbox.workspace_id)
    cfg = decrypt_json(mailbox.tokens_enc)
    reader = build_reader(mailbox)
    sha = file_sha(cfg["path"]) if mailbox.tipo == "mbox" else None
    since = None
    if mailbox.tipo != "mbox" and mailbox.ultima_varredura:
        since = mailbox.ultima_varredura - timedelta(days=OVERLAP_DIAS)
    return scan_mailbox(session, mailbox, reader, own_addresses=own_addresses(session, mailbox.workspace_id),
                        noise=NoiseFilter.from_config(ws.config), llm=build_llm(), since=since, content_sha=sha)


def _icp(session, ws: int, icp_id: int | None):
    from app.core.models import IcpProfile

    icp = session.get(IcpProfile, icp_id) if icp_id else None
    return icp if icp is not None and icp.workspace_id == ws else None


async def collect_cnpj_job(ctx, ws: int, icp_id: int) -> dict:
    from app.hunter.companies.service import run_cnpj

    with SessionLocal() as session:
        icp = _icp(session, ws, icp_id)
        return run_cnpj(session, ws, icp) if icp else {"erro": "perfil não existe"}


async def collect_places_job(ctx, ws: int, icp_id: int) -> dict:
    from app.hunter.companies.service import run_places

    with SessionLocal() as session:
        icp = _icp(session, ws, icp_id)
        return run_places(session, ws, icp) if icp else {"erro": "perfil não existe"}


async def crawl_sites_job(ctx, ws: int, icp_id: int | None = None, limite: int = 200) -> dict:
    from app.hunter.companies.service import run_crawl

    with SessionLocal() as session:
        return run_crawl(session, ws, _icp(session, ws, icp_id), limite)


async def collect_signals_job(ctx, ws: int, icp_id: int | None = None, limite: int = 200) -> dict:
    from app.hunter.signals.service import run_signals

    with SessionLocal() as session:
        return run_signals(session, ws, _icp(session, ws, icp_id), limite)


async def scan_mailbox_job(ctx, mailbox_id: int) -> dict:
    with SessionLocal() as session:
        mailbox = session.get(MailboxAccount, mailbox_id)
        if mailbox is None:
            return {"erro": "caixa não existe"}
        try:
            res = run_scan(session, mailbox)
            session.commit()
        except Exception:
            session.rollback()
            mailbox = session.get(MailboxAccount, mailbox_id)
            if mailbox:
                mailbox.saude = "erro"
                session.commit()
            raise
        return {"job_id": res.job_id, "repetido": res.repetido, "lidas": res.lidas,
                "interacoes_novas": res.interacoes_novas, "contatos_novos": res.contatos_novos}
