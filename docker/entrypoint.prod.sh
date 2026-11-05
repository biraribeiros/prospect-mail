#!/bin/sh
set -e
alembic upgrade head
python -m app.core.seed
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips='*'
