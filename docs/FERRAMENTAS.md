# Ferramentas escolhidas (skills, plugins e conectores)

Critério: usar só o que muda o resultado. Tudo abaixo já está disponível nesta máquina.

## Em todas as etapas
| Ferramenta | Para quê |
|---|---|
| Modo plano (nativo) | Apresentar o plano e esperar aprovação (regra 2) |
| `context7` (MCP) | Ler a documentação **atual** de FastAPI, HTMX, SQLAlchemy/Alembic, Playwright, httpx etc. |
| `run` + navegador embutido | Subir o app e conferir a tela de verdade |
| `engineering:testing-strategy` | Desenhar os testes das regras de negócio |
| `code-review` e `security-review` | Revisão ao fim de cada etapa (segredos, OAuth, SQL) |
| `engineering:documentation` | Manter `PROGRESSO.md` e os docs |
| `graft` (MCP) | Navegar o código quando o repositório crescer (a partir do Sprint 2) |

## Por etapa
| Etapa | Skills / plugins | Uso |
|---|---|---|
| 0 | `engineering:architecture`, `engineering:system-design`, `anthropic-skills:via-context`, `design:ux-copy`, `taste-skill:minimalist-skill` | ADRs e modelo de dados; semente do workspace VIA com contexto real; textos claros e visual limpo no painel |
| 1 | `claude-api`, `security-review` | Extração de assinatura com cache e custo; cuidado com tokens OAuth |
| 2a / 2b | `brightdata-plugin:scrape`, `brightdata-plugin:data-feeds`, `brightdata-plugin:search` (opcionais) | Possível adaptador licenciado para Maps/vagas/notícias — **depende de decisão de custo** |
| 3 | `engineering:system-design` | Regras de resolução e mesclagem reversível |
| 4 | `engineering:testing-strategy` | Verificador mockado, nunca chamada real |
| 5a / 5b | `claude-api`, `anthropic-skills:via-context`, `anthropic-skills:sales-enablement` | Hipóteses só com evidência; catálogo de ofertas e cases da VIA |
| 6a / 6b | `security-review`, `engineering:deploy-checklist` | Guard, DNS, disjuntor |
| 7 | `claude-api`, `anthropic-skills:copywriter`, `marketing:email-sequence`, `design:ux-copy` | Estrutura e tom dos emails em PT-BR |
| 8 | `marketing:email-sequence`, `claude-api` | Cadência e classificação de respostas |
| 9 | `dataviz`, `data:build-dashboard`, `data:statistical-analysis` | Painel e veredito A/B |
| 10 | `engineering:deploy-checklist`, `engineering:incident-response`, `engineering:tech-debt` | Deploy, runbook, dívida técnica |

## Avaliados e não usados
- **Plugins de CRM do catálogo** (Sales, monday, ActiveCampaign etc.): só fazem sentido no Sprint 8 **se** o seu CRM for um deles. O Prospector conversa com o CRM por webhook.
- **Stripe**: só se o Prospector virar SaaS (decisão 6).
- **Multiagentes/Workflow**: não são necessários; usar apenas se você pedir.
