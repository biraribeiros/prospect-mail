# Etapa 0 — Fundação

**Em uma frase:** o sistema abre no navegador, tem login, cadastros básicos e importação de planilhas.

## O que você terá no final
- Painel em `http://localhost:8000` com Hunter / Filtro / Inteligência / Disparo / Configurações ("em construção").
- Caixa de testes Mailpit em `http://localhost:8025`.
- Workspace "Agência VIA" já criado, com 10 departamentos prontos.
- Cadastro de perfis de cliente ideal (ICP) e de departamentos.
- Importar/exportar CSV de empresas e contatos.

## O que será feito
1. `docker-compose.yml` (app, worker, postgres, redis, mailpit, rspamd) e `.env.example`.
2. Banco com migrações. **Correção F1/F3:** além das 6 tabelas originais, criar já `company`, `contact`, `evidence`, `verification`, `contact_role`, `app_user`, `workspace_member`.
3. Semente: workspace VIA + departamentos + necessidades previstas (usar `anthropic-skills:via-context`).
4. Login (senha com argon2, CSRF, cookie seguro) e escolha de workspace.
5. Funções-base: `record_evidence`, `start_job/finish_job`, `create_compliance_record`.
6. Telas HTMX: ICP, departamentos, importação/exportação CSV.
7. `PROGRESSO.md`, ADR-001 (stack), ADR-002 (Prospector ↔ CRM), ADR-003 (fila ARQ isolada).

## Ferramentas
`engineering:architecture`, `engineering:system-design`, `anthropic-skills:via-context`, `design:ux-copy`, `taste-skill:minimalist-skill`, `context7`.

## Antes de começar
Docker Desktop aberto. Nenhuma decisão pendente.

## Como conferir que deu certo
```bash
docker compose up -d
pytest tests/core/ -v
```
- Abrir `localhost:8000` e `localhost:8025`.
- Criar um ICP pelo painel.
- Importar CSV de 100 contatos → cada um tem evidência (origem "importacao_manual") e registro de conformidade.
- Teste: workspace A não vê dados do B.

## Atenção
`DRY_RUN=true` fixo: nada é enviado de verdade.
