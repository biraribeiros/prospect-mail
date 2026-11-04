# Validação do plano (Prompt Mestre v3.0)

**Veredito:** plano sólido e bem pensado. Tem **1 erro bloqueante**, **4 ajustes importantes** e alguns alertas. Correções **aprovadas em 04/11/2026** e aplicadas em `PROMPT_MESTRE.md` (v3.1).

## Erros de sequência (corrigir)
| # | Problema | Correção proposta |
|---|---|---|
| F1 🔴 | O Sprint 0 pede importar CSV de contatos/empresas e gravar `evidence`, mas as tabelas `company`, `contact` e `evidence` só nascem no Sprint 2. O critério de aceite do Sprint 0 **não é executável**. | Criar `company`, `contact`, `evidence`, `verification` e `contact_role` já no Sprint 0. |
| F2 🟠 | `mailbox_account` está no Sprint 6, mas o Sprint 1 já conecta Gmail/IMAP. | Criar no Sprint 1. |
| F3 🟠 | `contact_role` é usada nos Sprints 2–3, mas só é criada no 5b. | Entra no Sprint 0 (ver F1). |
| F4 🟠 | Faltam tabelas: `app_user` e `workspace_member` (login no Sprint 0, papéis no 10), `api_cache` (cache Places 30d / verificação 90d), `webhook_log` (Sprint 8), `message_template`. | Adicionar nos sprints onde surgem. |
| F5 🟠 | Sprints 2, 5 e 6 são grandes demais para "um por sessão". | Dividir em 2a/2b, 5a/5b, 6a/6b (14 etapas no total). |

## Segurança e regras de negócio
| # | Alerta | Ação |
|---|---|---|
| F6 🟠 | `guard.py` decide "domínio principal" pelo campo `sending_domain.tipo`, que o usuário preenche — dá para burlar errando o cadastro. | O guard deve **derivar** o domínio principal dos domínios das caixas lidas pelo Hunter + lista fixa do workspace; `tipo` imutável após o 1º envio; teste obrigatório. |
| F7 🟡 | Login: faltam hash de senha forte, CSRF nos formulários HTMX e cookie `HttpOnly/Secure/SameSite`. | Incluir no aceite do Sprint 0 (argon2). |
| F8 🟡 | Isolamento multi-tenant sem teste. | Teste: workspace A nunca enxerga dados do B. |
| F9 🟡 | Chave Fernet sem plano de troca. | Usar `MultiFernet` (rotação) e guardar a chave só no `.env`. |

## Dependências externas e riscos
| # | Tema | Fato verificado / ação |
|---|---|---|
| F10 🟠 | **LGPD**: dados de sócios e contatos são dados pessoais; a "base legal" precisa de avaliação (legítimo interesse). | Criar `docs/LGPD.md` (teste de legítimo interesse + modelo de aviso/opt-out) **antes do Sprint 1**. Recomendada revisão de advogado. |
| F11 🟠 | **Vagas via LinkedIn/Indeed/Catho**: a raspagem costuma violar os termos de uso e é bloqueada. | Detector de vagas atrás de adaptador; começar por RSS, páginas "trabalhe conosco" e Google News RSS; fornecedor licenciado (ex.: Apify/Bright Data) como decisão de custo. |
| F12 🟠 | **Gmail `gmail.readonly` é escopo restrito**: app público exige verificação + auditoria anual (CASA) ([fonte](https://developer.nylas.com/docs/cookbook/use-cases/build/google-oauth-scopes/)). | Se a VIA usa Google Workspace, configurar a tela de consentimento como **Interna** (em geral dispensa verificação — *não confirmado em fonte; checar a doc do Google*). Para caixas de terceiros: IMAP com senha de app. |
| F13 🟡 | **ARQ está em modo manutenção** (sem novidades; alternativas citadas: SAQ/Streaq) ([GitHub](https://github.com/python-arq/arq)). | Manter (stack inegociável), mas isolar atrás de `workers/queue.py` e registrar **ADR-003** com plano de troca. |
| F14 🟠 | **Porta 25**: provedores como a Hetzner bloqueiam por padrão; a liberação exige pedido, e os prazos variam entre fontes (*confirmar na FAQ oficial da Hetzner*). Rede doméstica/Windows também não tem porta 25 livre. | Abrir conta no VPS e **pedir a liberação antes do Sprint 4** (não do 10). Em dev: Reacher mockado + API paga como fallback. |
| F15 🟡 | A decisão "Reacher AGPL vs comercial" é necessária no **Sprint 4**, não no 6. | Mover a decisão. |
| F16 🟡 | A meta "20k contatos filtrados em <30 min" é limitada pela verificação SMTP (máx. 3 simultâneas por MX). | Definir o teste com latência simulada e fixar a meta depois de medir. |
| F17 🟡 | O teste "Gmail com 1.000 mensagens" precisa de conta de teste. | Usar conta de teste ou arquivo `.mbox` de amostra. |

## Decisões que só você pode tomar
Domínios secundários · provedor de caixas (Google/Microsoft/SMTP) · Maps (Places ou Apify) · Reacher (AGPL ou comercial) · qual CRM e qual webhook · se o Prospector vira produto SaaS · quem revisa a LGPD.
