# ADR-002 — Separação entre Prospector e CRM

**Status:** aceita (04/11/2026)

## Decisão
O Prospector **qualifica e prospecta**; o CRM externo **gerencia as negociações**. A ligação é feita por webhook/API (Etapa 8), enviando só oportunidades reais (resposta positiva, deal criado, mudança de estágio, ganho/perdido) e evitando duplicidade por `crm_externo_id`.

## Por quê
A equipe já usa um CRM. Duplicar funil e histórico geraria dois "donos da verdade". Sincronizar todo contato pesquisado poluiria o CRM com dados frios.

## Consequências
- O Prospector mantém apenas um painel mínimo de deals e tasks (fallback quando o CRM está fora do ar).
- Falhas de webhook entram em fila com tentativas e log (`webhook_log`).
- Decisão pendente: qual CRM, endpoint e mapeamento de campos (antes da Etapa 8).
