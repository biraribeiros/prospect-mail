# ADR-001 — Stack

**Status:** aceita (04/11/2026)

## Decisão
Python 3.12 · FastAPI + Jinja2 + HTMX (sem build de frontend) · PostgreSQL 16 + `pg_trgm` · Redis + ARQ · httpx (Playwright só quando houver JavaScript obrigatório) · SQLAlchemy 2 + Alembic · Docker Compose · Mailpit (testes de email) · Rspamd (nota de spam).

## Por quê
Uma stack só em Python, simples de operar por uma pessoa e sem etapa de build. Postgres cobre dados relacionais, JSON e busca por similaridade (`pg_trgm`) sem serviços extras.

## Detalhes da Etapa 0
- SQLAlchemy síncrono: o painel é um CRUD simples; o worker (ARQ) é o único código assíncrono.
- HTMX é carregado de um CDN com versão fixa (2.0.4). Se precisarmos funcionar offline, copiar o arquivo para `app/web/static/`.
- Sessão por cookie assinado (`HttpOnly`, `SameSite=Lax`, `Secure` em produção via `SESSION_HTTPS_ONLY`), senha com argon2 e CSRF em todo POST.
- Dependências com faixas de versão em `requirements.txt`; fixar versões (lock) antes da produção.

## Consequência
Trocar qualquer item exige um novo ADR com justificativa técnica.
