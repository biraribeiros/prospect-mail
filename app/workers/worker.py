from app.workers.queue import redis_settings


async def ping(ctx) -> str:
    """Job de teste: confirma que o worker está vivo."""
    return "pong"


class WorkerSettings:
    functions = [ping]
    redis_settings = redis_settings()
