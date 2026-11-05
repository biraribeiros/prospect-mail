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
