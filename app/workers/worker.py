from app.workers.jobs import (collect_cnpj_job, collect_places_job, collect_signals_job, crawl_sites_job,
                              resolve_job, scan_mailbox_job)
from app.workers.queue import redis_settings


async def ping(ctx) -> str:
    """Job de teste: confirma que o worker está vivo."""
    return "pong"


class WorkerSettings:
    functions = [ping, scan_mailbox_job, collect_cnpj_job, collect_places_job, crawl_sites_job, collect_signals_job,
                 resolve_job]
    job_timeout = 6 * 3600  # base CNPJ e coletas grandes podem demorar
    max_tries = 1  # uma coleta que falhou não deve se repetir sozinha (poderia gastar de novo)
    redis_settings = redis_settings()
