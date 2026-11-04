# Progresso

Leia também: [VALIDACAO_DO_PLANO.md](VALIDACAO_DO_PLANO.md) (correções aprovadas, já no Prompt Mestre v3.1).

| Etapa | Status | Branch | Observações |
|---|---|---|---|
| Planejamento e validação | ✅ feito | main | Correções F1–F17 aprovadas (Prompt Mestre v3.1) |
| 0 Fundação | ✅ feita (aguarda merge) | `sprint-00-fundacao` | 66 testes passando; aceite conferido (ver abaixo) |
| 1 Caixas de email | ⬜ | | LGPD adiada até comercialização |
| 2a Empresas | ⬜ | | |
| 2b Sinais | ⬜ | | Decisão: fonte de vagas |
| 3 Duplicados | ⬜ | | |
| 4 Validar emails | ⬜ | | Começa com API paga; Reacher só com VPS |
| 5a Enriquecimento | ⬜ | | |
| 5b Inteligência | ⬜ | | |
| 6a Envio: cadastro | ⬜ | | Caixas SMTP via cPanel/Plesk; decidir domínios |
| 6b Envio: agenda | ⬜ | | |
| 7 Mensagens | ⬜ | | |
| 8 Respostas e CRM | ⬜ | | Decisão: qual CRM |
| 9 Métricas | ⬜ | | |
| 10 Produção | ⬜ | | |

## Etapa 0 — o que foi feito
- Docker Compose com `app`, `worker`, `postgres`, `redis`, `mailpit`, `rspamd`; `.env.example` documentado; `.env` fora do Git.
- Migração `0001_fundacao` (banco zerado sem erro): `workspace`, `app_user`, `workspace_member`, `icp_profile`, `department`, `job_run`, `suppression`, `compliance_record`, `company`, `contact`, `evidence`, `verification`, `contact_role` + extensão `pg_trgm`.
- Semente: workspace "Agência VIA", 10 departamentos com necessidades previstas e usuário admin (a partir do `.env`).
- Login (argon2, CSRF, cookie seguro, limite de tentativas), seleção de workspace, papéis admin/operador/aprovador nos cadastros.
- Funções-base: `record_evidence`, `start_job/finish_job` (idempotente + custo), `create_compliance_record`, criptografia `MultiFernet`.
- Painel HTMX: 5 seções ("Sprint N — Em construção"), CRUD de ICP e departamentos, importação/exportação CSV de empresas e contatos.
- ADRs 001 (stack), 002 (Prospector × CRM), 003 (fila isolada).

## Como validar (comandos)
```bash
docker compose up -d --build
docker compose run --rm app pytest tests/core/ -v          # 66 testes
docker compose exec app python scripts/verificar_aceite.py # aceite ponta a ponta
```
- Painel: http://localhost:8000 · Mailpit: http://localhost:8025
- Importar `samples/contatos_100.csv` em Configurações → Importar.

## Resultado do aceite (04/11/2026)
- `docker compose up -d` sobe todos os serviços; `/health` responde; Mailpit em `:8025`.
- ICP criado pelo painel; CSV de 100 contatos → 100 contatos, 100 evidências `importacao_manual`, 100 registros de conformidade; reimportar o mesmo arquivo não duplica.
- Worker ARQ recebeu e executou um job `ping`.
- Painel conferido no navegador (login, início, lista de ICP com HTMX carregado).

## Decisões e desvios
- 04/11/2026: LGPD adiada até comercialização (a base legal fica "legitimo_interesse_a_avaliar").
- 04/11/2026: sem VPS agora; caixas de envio nas hospedagens cPanel/Plesk; Etapa 4 com API paga.
- A skill `anthropic-skills:via-context` **não** foi usada: as necessidades dos departamentos são genéricas para agência de comunicação e editáveis no painel. Revisar na Etapa 5b com ofertas e cases reais da VIA.
- `app/core/csvio.py` importa as listas de UF/porte de `app/web/forms.py` (acoplamento núcleo→web). Mover para `app/core/` quando surgir o próximo consumidor.
- Dependências em faixas de versão; gerar lock antes da produção.
- Não foi rodada a skill `security-review` completa; foi feita revisão manual dos pontos de risco (sessão, CSRF, isolamento entre workspaces, CSV). Rodar `security-review` no fim da Etapa 1 (OAuth/tokens).

## Pendências para as próximas etapas
- Etapa 1: tela do Hunter e `mailbox_account` (+ tokens cifrados com `encrypt_json`).
- O limite de tentativas de login é em memória (reinicia com o app); usar Redis quando houver mais de uma instância.
