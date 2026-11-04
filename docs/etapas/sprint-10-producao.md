# Etapa 10 — Produção e multi-workspace

**Em uma frase:** rodar em servidor real, para vários workspaces, sem intervenção manual.

## O que você terá no final
- `docs/DEPLOY.md` e `docs/OPERACAO.md` passo a passo.
- Backup diário (30 dias), alerta de jobs presos, rotação de logs.
- Limites por workspace (contatos, envios/dia, gasto de API).
- Papéis: Admin, Operador, Aprovador.
- Renovação automática de tokens OAuth.
- Teste de carga e um segundo workspace de outro setor funcionando sem mexer no código.

## Ferramentas
`engineering:deploy-checklist`, `engineering:incident-response`, `engineering:tech-debt`, `security-review`.

## Antes de começar
Piloto comercial realizado (volume não é prova de sucesso). Decidir se o Prospector vira SaaS (cobrança por workspace).

## Como conferir que deu certo
```bash
pytest tests/ -v --tb=short
```
- Zero falhas.
- Segundo workspace percorre Hunter → Filtro → Inteligência → Disparo.
- Carga: meta definida após medir o gargalo SMTP (F16).
