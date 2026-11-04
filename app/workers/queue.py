"""Fila de jobs isolada (ADR-003): o resto do código só conhece `enqueue`.

Hoje usa ARQ (modo manutenção). Para trocar por SAQ/Streaq, basta reescrever este arquivo.
"""
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings

from app.core.config import get_settings


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


async def enqueue(job_name: str, *args: Any, **kwargs: Any) -> str | None:
    pool = await create_pool(redis_settings())
    try:
        job = await pool.enqueue_job(job_name, *args, **kwargs)
        return job.job_id if job else None
    finally:
        await pool.aclose()
