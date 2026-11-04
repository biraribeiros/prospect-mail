# ADR-003 — Fila de jobs isolada (ARQ em modo manutenção)

**Status:** aceita (04/11/2026)

## Contexto
A stack define Redis + ARQ. Em consulta de 04/11/2026, o projeto ARQ está em **modo manutenção** (sem novidades; última versão confirmada 0.27.0, fev/2026). Não há aviso oficial de descontinuação; páginas de terceiros sugerem SAQ ou Streaq como alternativas ([GitHub do ARQ](https://github.com/python-arq/arq)).

## Decisão
Manter o ARQ, mas **todo código fala só com `app/workers/queue.py`** (`enqueue`). As funções de job ficam em `app/workers/` e não dependem de recursos específicos do ARQ.

## Consequência
Trocar para SAQ/Streaq mexe em `queue.py` e `worker.py`, sem tocar na regra de negócio. Reavaliar se surgir incompatibilidade com Python/Redis novos.
