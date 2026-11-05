from app.workers.jobs import scan_mailbox_job
from app.workers.queue import redis_settings


async def ping(ctx) -> str:
    """Job de teste: confirma que o worker está vivo."""
    return "pong"


class WorkerSettings:
    functions = [ping, scan_mailbox_job]
    job_timeout = 3600  # varrer uma caixa grande pode demorar
    redis_settings = redis_settings()
