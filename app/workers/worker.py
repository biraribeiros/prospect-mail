from app.workers.jobs import (collect_cnpj_job, poll_replies_job, compose_job, dispatch_all_job, dispatch_workspace_job, enrich_job, hypotheses_job, collect_places_job, collect_signals_job, crawl_sites_job,
                              resolve_job, scan_mailbox_job, validate_emails_job)
from arq import cron

from app.workers.queue import redis_settings


async def ping(ctx) -> str:
    """Job de teste: confirma que o worker está vivo."""
    return "pong"


class WorkerSettings:
    functions = [ping, scan_mailbox_job, collect_cnpj_job, collect_places_job, crawl_sites_job, collect_signals_job,
                 resolve_job, validate_emails_job, enrich_job, hypotheses_job,
                 dispatch_workspace_job, dispatch_all_job, compose_job, poll_replies_job]
    cron_jobs = [cron(dispatch_all_job, second=0),  # a cada minuto
                 cron(poll_replies_job, minute={0, 10, 20, 30, 40, 50}, second=30)]  # respostas a cada 10 min
    job_timeout = 6 * 3600  # base CNPJ e coletas grandes podem demorar
    max_tries = 1  # uma coleta que falhou não deve se repetir sozinha (poderia gastar de novo)
    redis_settings = redis_settings()
