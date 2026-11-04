# PROSPECTOR B2B — Prompt Mestre v3.0
### Plataforma de Inteligência Comercial: Hunter · Filtro · Inteligência · Disparo

---

## COMO USAR ESTE ARQUIVO

**Primeira sessão:** cole a **Seção 1 (Contexto)** + o **Sprint 0** abaixo.

**Sessões seguintes:** diga exatamente isso:
> *"Leia docs/PROMPT_MESTRE.md e docs/PROGRESSO.md e execute o Sprint N. Não faça nada além do sprint pedido."*

**Regra de ouro:** um sprint por sessão · um branch por sprint · apresente o plano antes de codar.

---

## SEÇÃO 1 — CONTEXTO PERMANENTE

---

### O QUE É O PROSPECTOR

Plataforma B2B de inteligência comercial e prospecção, **generalista por design**.
A Agência VIA é o primeiro workspace — não o produto.
Todo comportamento específico de setor vive em configuração, não em código.

**O sistema responde uma pergunta central antes de qualquer contato:**
> *"Por que devemos abordar esta empresa, esta pessoa, neste departamento, agora?"*

O sucesso se mede por **oportunidades geradas, reuniões realizadas e negócios ganhos** — não por volume de emails enviados.

**Quatro módulos em sequência:**

```
HUNTER     → encontra empresas, contatos e sinais comerciais
FILTRO     → valida, enriquece e qualifica cada dado
INTELIG.   → relaciona sinais com ofertas e monta hipóteses de oportunidade
DISPARO    → redige, aprova, envia, lê respostas e alimenta o funil
```

---

### REGRAS DE TRABALHO — OBRIGATÓRIAS

1. **Execute somente o sprint pedido.** Dependências futuras viram stubs com interface definida, registradas em `docs/PROGRESSO.md`.
2. **Apresente o plano antes de codar.** Liste arquivos a criar/alterar, tabelas novas e decisões pendentes. Aguarde aprovação explícita.
3. **Zero email real em dev.** Todo envio usa adaptador. Em dev: Mailpit. `DRY_RUN=true` é o padrão hardcoded — só `false` explícito envia de verdade.
4. **Toda fonte externa é um adaptador plugável.** Interface única, múltiplas implementações. Nenhuma lógica de negócio acoplada a fornecedor.
5. **Proveniência em tudo.** Todo dado persistido registra: fonte, URL, confiança (0–1), data de coleta. Use a tabela `evidence`.
6. **Idempotência.** Rodar o mesmo job duas vezes não duplica dados. `job_run` deduplica por hash dos parâmetros.
7. **IA usa somente dados com evidência registrada.** Zero fatos inventados em mensagens comerciais.
8. **Testes para regras de negócio.** Validação, deduplicação, limites de envio, transições de funil. Mocks — nunca chamadas reais em testes.
9. **Fim de sprint:** atualize `docs/PROGRESSO.md` com feito, pendente, decisões e comandos de validação.
10. **Segredos só em `.env`.** Tokens OAuth cifrados no banco (Fernet). Nunca em log.
11. **Custo de API registrado.** Toda chamada a LLM, Google Places ou verificador de email registra custo estimado no `job_run`.
12. **Conformidade documental.** Toda abordagem registra: origem do dado, finalidade, base legal avaliada, data de coleta. Descadastros processados imediatamente.
13. **Cold email só por domínio secundário.** Bloqueio em código (`disparo/senders/guard.py`), não configurável pelo usuário.
14. **CRM externo.** O Prospector qualifica e envia para o CRM via webhook/API. Não substitui o CRM já em andamento. Sincroniza apenas registros comerciais relevantes — não cada contato pesquisado.

---

### STACK — NÃO NEGOCIAR

| Camada | Tecnologia |
|---|---|
| Linguagem | Python 3.12 |
| API + painel | FastAPI + Jinja2 + HTMX (sem build de frontend) |
| Banco | PostgreSQL 16 + extensão `pg_trgm` |
| Filas | Redis + ARQ |
| HTTP | httpx (padrão) + Playwright (só quando JS obrigatório) |
| Verificação SMTP | Reacher (Docker, porta 25) + adaptador API paga como fallback |
| LLM | Claude API via adaptador com cache e registro de custo |
| Spam score | Rspamd (Docker, pontua — não filtra) |
| Sandbox email | Mailpit |
| Infra | Docker Compose |

Substituição de tecnologia exige justificativa técnica documentada em ADR.

---

### ESTRUTURA DE PASTAS — CRIAR EXATAMENTE ASSIM

```
prospector/
  app/
    core/              # config, db, auth, workspaces, custo, proveniência, compliance
    hunter/
      mailbox/         # adaptadores: Gmail API, Graph, IMAP, .mbox/.eml
      companies/       # adaptadores: CNPJ Receita, Google Places, Apify, crawler
      signals/         # detecção de sinais comerciais (vagas, expansões, eventos, notícias)
      resolution/      # deduplicação e vínculo empresa ↔ contato
    filtro/
      validation/      # sintaxe → MX → descartável → SMTP → catch-all
      enrichment/      # nome, cargo, departamento, sócios, padrões de email
      liveness/        # site ativo, redes, Maps, situação cadastral
      scoring/         # scores compostos (empresa, contato, email, oportunidade)
    intel/             # NOVO: camada de inteligência comercial
      offers/          # catálogo de produtos e serviços por workspace
      needs/           # necessidades prováveis por departamento
      hypothesis/      # geração de hipóteses de oportunidade
    disparo/
      senders/         # caixas, domínios, aquecimento, limites, guard, disjuntor
      compose/         # templates, personalização IA, checagem de spam, aprovação
      sequences/       # cadências, agendamento, regras de parada
      inbox/           # leitura de respostas, bounces, descadastros
      crm/             # integração CRM: webhook out, deduplicação, rastreabilidade
    web/               # rotas FastAPI + templates HTMX
    workers/           # jobs ARQ
  docs/
    PROMPT_MESTRE.md
    PROGRESSO.md
    DEPLOY.md
    OPERACAO.md
    adr/               # Architecture Decision Records
  tests/
  docker/
    rspamd/
    mailpit/
  docker-compose.yml
  .env.example
  alembic/
```

---

### MODELO DE DADOS — TODAS AS TABELAS

Crie as tabelas do Sprint 0 no Sprint 0. Sprints futuros adicionam colunas/tabelas sem recriar.

**Tabelas do Sprint 0 (fundação):**

| Tabela | Campos essenciais |
|---|---|
| `workspace` | id, nome, config JSONB, limites JSONB, chaves_api_enc JSONB |
| `icp_profile` | workspace_id, nome, cnaes[], ufs[], municipios[], portes[], termos_maps[], departamentos_alvo[] |
| `department` | workspace_id, nome, email_patterns[], cargos[], necessidades_previstas JSONB |
| `job_run` | tipo, parametros_hash, parametros JSONB, status, contadores JSONB, custo_usd, iniciado_em, finalizado_em |
| `suppression` | workspace_id, email_ou_dominio, motivo, origem, criado_em |
| `compliance_record` | entidade_tipo, entidade_id, workspace_id, origem_dado, finalidade, base_legal, coletado_em, retencao_ate, historico_oposicao JSONB |

**Tabelas do Sprint 2 (empresas e contatos):**

| Tabela | Campos essenciais |
|---|---|
| `company` | workspace_id, cnpj, razao_social, nome_fantasia, dominio, site, cnae_principal, cnae_secundarios[], municipio, uf, porte, data_abertura, situacao_cadastral, liveness_score, fit_score, setor_humano, gatilhos_previaveis[] |
| `contact` | workspace_id, company_id, nome_completo, primeiro_nome, forma_tratamento, cargo, department_id, email, email_type, email_status, relationship_score, origem |
| `evidence` | entidade_tipo, entidade_id, campo, valor, fonte, url, confianca, coletado_em |
| `verification` | contact_id, camada, resultado, detalhes JSONB, provedor, verificado_em |

**Tabela do Sprint 2b (sinais comerciais — Hunter ampliado):**

| Tabela | Campos essenciais |
|---|---|
| `opportunity_signal` | company_id, signal_type (vaga/expansao/evento/noticia/lancamento/mudanca_diretoria/rebranding/atividade_recente), descricao, source, source_url, confianca, detectado_em, expira_em, status (ativo/expirado/usado) |

**Tabelas do Sprint 5b (inteligência comercial):**

| Tabela | Campos essenciais |
|---|---|
| `commercial_offer` | workspace_id, nome, descricao, segmentos_aplicaveis[], departamentos_relacionados[], problemas_que_resolve[], necessidades_atendidas[], ticket_min, ticket_max, cases_disponiveis JSONB, status (ativo/inativo) |
| `department_need` | department_id, workspace_id, necessidade, relevancia (1-5), ofertas_relacionadas[] |
| `contact_role` | contact_id, papel (decisor/influenciador/usuario/orcamento/aprovador/gatekeeper), confianca, fonte |
| `outreach_feedback` | message_id, deal_id, classificacao, objecoes[], aprendizados JSONB, registrado_em |

**Tabelas do Sprint 5b (hipótese de oportunidade):**

| Tabela | Campos essenciais |
|---|---|
| `opportunity_hypothesis` | workspace_id, company_id, contact_id, department_id, offer_id, signal_ids[], score_oportunidade, justificativa, status (rascunho/aprovada/abordada/convertida/descartada), criado_em |

**Tabelas do Sprint 6+ (disparo):**

| Tabela | Campos essenciais |
|---|---|
| `mailbox_account` | workspace_id, tipo, uso, dominio, tokens_enc, limite_diario, fase_aquecimento, saude |
| `sending_domain` | dominio, spf_ok, dkim_ok, dmarc_ok, tipo (principal/secundario), ultima_checagem |
| `campaign` | workspace_id, icp_id, nome, status |
| `sequence` | campaign_id, nome |
| `sequence_step` | sequence_id, dia_offset, tipo (sonda/seguimento/ultimo), template_id |
| `message` | contact_id, hypothesis_id, step_id, mailbox_id, assunto, corpo_text, status, agendado_para, message_id_header |
| `email_event` | message_id, tipo (enviado/bounce_hard/bounce_soft/resposta/descadastro/reclamacao), payload JSONB |
| `reply` | message_id, texto, classificacao (interessado/nao_agora/pessoa_errada/ausente/descadastro/objecao), confianca |
| `deal` | workspace_id, contact_id, company_id, hypothesis_id, stage, valor_estimado, proxima_acao, vencimento, crm_externo_id |
| `task` | workspace_id, deal_id, descricao, status, vencimento |

---

### QUATRO AVALIAÇÕES INDEPENDENTES (Filtro v3)

O score de um prospect é **quatro notas separadas**, nunca colapsadas em um único número sem preservar os componentes:

**A. Qualidade da empresa (fit_score 0–100)**
- CNAE no ICP: +40
- Porte no ICP: +20
- UF/município no ICP: +20
- Situação cadastral ativa: +20

**B. Qualidade do contato (contact_quality 0–100)**
- Nome identificado: +20
- Cargo confirmado: +20
- Departamento mapeado: +20
- Papel na compra definido: +20
- Contato vinculado à empresa com evidência: +20

**C. Qualidade do email (email_score 0–100)**
- Sintaxe válida: +10
- MX resolve: +20
- Não descartável: +20
- SMTP verificado: +30
- Não catch-all: +20
- (Catch-all detectado: email_score máx = 60, status = arriscado)

**D. Qualidade da oportunidade (opportunity_score 0–100)**
- Sinal comercial detectado: +30
- Necessidade do departamento mapeada: +25
- Oferta compatível cadastrada: +25
- Evidências suficientes para abordagem não genérica: +20

**prospect_score final** = fit × 0.30 + contact × 0.25 + email × 0.20 + opportunity × 0.25

---

### FLUXO COMPLETO v3

```
FONTES
──────
Caixas de email (Gmail / Outlook / IMAP / .mbox)
Base CNPJ — Receita Federal (50M empresas)
Google Places / Apify Maps
Crawler de sites institucionais
Monitoramento de vagas, eventos, notícias (HTTP + RSS)
         │
         ▼
    [ HUNTER ]
    Coleta: empresa, contato, email, telefone, sócios, assinatura
    Sinais: vagas abertas, expansões, eventos, notícias, mudanças de diretoria
    Relacionamento prévio: relationship_score por bidirecionalidade e recência
    Ruído filtrado: noreply, contadores, sistemas, marketplaces
         │
         ▼
    [ FILTRO ]
    Deduplicação: CNPJ > domínio > telefone > nome+município (pg_trgm ≥ 0.85)
    4 avaliações independentes: empresa · contato · email · oportunidade
    Enriquecimento: nome, cargo, departamento, sócios, padrões de email
    Liveness: site HTTPS, ano no rodapé, blog recente, Maps, redes sociais
    compliance_record criado para todo contato antes de qualquer abordagem
         │
         ▼
    [ INTELIGÊNCIA COMERCIAL ]
    Relaciona: empresa + departamento + sinal + necessidade + oferta
    Responde: "Por que abordar agora?"
    Gera hypothesis com justificativa e score de oportunidade
    Fila de aprovação de hipóteses antes de gerar mensagens
         │
         ▼
    [ DISPARO ]
    IA redige usando SOMENTE dados com evidência registrada
    Estrutura: motivo específico → contexto → problema/oportunidade →
               oferta → prova real → CTA único
    Rspamd pontua → fila de aprovação humana obrigatória
    Agendador: rodízio de caixas + janela comercial + intervalo aleatório
    Sequência: dia 0 (sonda) → dia 4 (seguimento) → dia 10 (último toque)
    Cabeçalhos: List-Unsubscribe RFC 8058, Message-ID próprio, texto simples
    Disjuntor: pausa se bounce > 3% / 24h ou reclamação detectada
         │
         ▼
    [ RESPOSTAS ]
    Leitura das caixas de envio (mesmo adaptador do Hunter)
    Classificação por IA:
      interessado    → webhook CRM + task "WhatsApp 24h"
      nao_agora      → reagenda 90 dias
      pessoa_errada  → novo contato + vincula empresa
      ausente        → pausa até data de retorno extraída
      descadastro    → suppression imediata + compliance_record
      objecao        → outreach_feedback + tag de tipo
         │
         ▼
    [ FUNIL / CRM ]
    Webhook de saída para CRM externo (configurável por workspace)
    Deduplicação por crm_externo_id antes de enviar
    Painel interno: deals + tasks + histórico por contato e empresa
    Métricas: aquisição · abordagem · conversão · inteligência
```

---

### PRINCÍPIOS DE PRODUTO

- **Multi-tenant desde o primeiro commit.** `workspace_id` em toda tabela de negócio.
- **Cold email só por domínio secundário.** Bloqueio em código, não em configuração.
- **Domínio secundário ≠ máscara de identidade.** O remetente é transparente e identificado.
- **Aprovação humana obrigatória antes do envio.** IA redige, pessoa aprova em lote.
- **Abertura não é métrica confiável** (Apple Mail Privacy). Prioridade: resposta, clique, bounce.
- **Silêncio não é prova de que o email não funciona.** Apenas respostas e bounces confirmam.
- **Conformidade documental é pré-requisito da abordagem**, não checkbox final.
- **Hipótese antes de mensagem.** Nenhuma mensagem gerada sem hypothesis aprovada.
- **Sócios do CNPJ = decisor de graça.** Nome do sócio + domínio = padrões verificáveis.
- **Email de contador detectado automaticamente.** Bloqueado para envio com badge no painel.
- **CRM é externo.** Prospector qualifica; CRM gerencia. Sincronização via webhook com controle de duplicidade.

---

## SEÇÃO 2 — SPRINTS

> **Regra:** apresente o plano antes de codar. Execute um sprint por sessão. Não inicie sprint seguinte sem aprovação.

---

### SPRINT 0 — Fundação

**Meta:** repositório rodando, banco criado, painel navegável, multi-tenant funcionando.

**Entregáveis:**

1. `docker-compose.yml`: `app` (FastAPI), `worker` (ARQ), `postgres`, `redis`, `mailpit`, `rspamd`
2. `.env.example` documentado. `.env` no `.gitignore`.
3. Alembic configurado. Migrações para tabelas do Sprint 0: `workspace`, `icp_profile`, `department`, `job_run`, `suppression`, `compliance_record`.
4. Seed: workspace "Agência VIA" + taxonomia de departamentos:
   - Marketing e Comunicação, RH e Gente, SST e Segurança do Trabalho, Comercial e Vendas, Eventos, Operações e Qualidade, Diretoria, Compras e Financeiro, TI, Jurídico
   - Cada departamento com `necessidades_previstas` JSONB pré-configuradas para o contexto de agência de comunicação
5. Autenticação: usuário + senha + sessão cookie. Seleção de workspace.
6. `core/provenance.py`: `record_evidence(entity_type, entity_id, field, value, source, url, confidence)`.
7. `core/jobs.py`: `start_job(tipo, parametros) → job_run_id`. `finish_job(id, contadores, custo)`.
8. `core/compliance.py`: `create_compliance_record(entity_type, entity_id, workspace_id, origem, finalidade, base_legal)`.
9. Painel HTMX: Hunter / Filtro / Inteligência / Disparo / Configurações. Cada seção: "Sprint N — Em construção."
10. CRUD: Perfis de ICP (nome, CNAEs, UFs, municípios, portes, departamentos-alvo).
11. CRUD: Departamentos (nome, padrões de email, cargos, necessidades_previstas).
12. Importação e exportação CSV de empresas e contatos.
13. `docs/PROGRESSO.md` criado. ADR-001: stack. ADR-002: separação Prospector ↔ CRM.

**Critério de aceite:**
```bash
docker compose up -d
# http://localhost:8000 sobe ✓
# http://localhost:8025 Mailpit sobe ✓
# Criar ICP pelo painel ✓
# Importar CSV de 100 contatos → evidence com origem "importacao_manual" ✓
# compliance_record criado para cada contato importado ✓
# Migrações em banco zerado sem erro ✓
# pytest tests/core/ -v ✓
```

---

### SPRINT 1 — Hunter: Caixas de Email

**Meta:** ler caixas próprias e gerar contatos com score de relacionamento.

**Entregáveis:**

1. Interface `MailboxReader` em `hunter/mailbox/base.py`: `scan(since: datetime) → Iterator[RawMessage]`.
2. Adaptadores:
   - `GmailAdapter`: OAuth2, `gmail.readonly`, leitura incremental por `historyId`.
   - `GraphAdapter`: Microsoft Graph, `Mail.Read`, IMAP com XOAUTH2.
   - `ImapAdapter`: IMAP genérico (Horde, Locaweb). SSL e STARTTLS.
   - `MboxAdapter`: arquivos `.mbox` e `.eml`.
3. Varredura inclui **Enviados** (para bidirecionalidade).
4. Filtro de ruído editável por workspace: `noreply`, `no-reply`, `List-Unsubscribe`, domínios de bancos, notificações, redes sociais, marketplaces.
5. Parser de assinatura: regex para nome/cargo/telefone/site. Claude API como fallback (custo registrado).
6. `relationship_score` (0–100): volume × bidirecionalidade × recência × palavras-chave (orçamento, proposta, contrato, NF, reunião).
7. Classificação de domínio: corporativo vs pessoal.
8. `compliance_record` criado para cada contato extraído de caixa própria.
9. Tela de revisão: contatos por score, filtro por fonte/domínio, exportação CSV.
10. **Guardar apenas metadados e trecho de assinatura. Nunca o corpo completo.**

**Critério de aceite:**
```bash
# Gmail conectado → 1.000 mensagens → contatos sem duplicatas ✓
# Reprocessar → zero registros novos (idempotência) ✓
# compliance_record presente para cada contato ✓
# pytest tests/hunter/test_mailbox.py -v ✓
```

---

### SPRINT 2 — Hunter: Empresas e Sinais Comerciais

**Meta:** montar lista de empresas de um ICP com sinais de oportunidade.

**Entregáveis:**

**2a — Fontes de empresas:**
1. **Importador CNPJ Receita Federal**: download incremental, staging, filtro por ICP, promoção para `company`, QSA como `contact` (papel: socio/administrador).
2. **Adaptador Google Places (New)**: Text Search, máscara mínima de campos, estimativa de custo antes de rodar, cache 30 dias.
3. **Adaptador Apify Maps**: alternativa de custo menor.
4. **Crawler de sites**: home, /contato, /sobre, /equipe, rodapé. Extrai emails (inclusive ofuscados), telefones, WhatsApp, CNPJ, redes sociais. `httpx` por padrão; Playwright só se JS obrigatório. Respeita `robots.txt`. Rate limit 1 req/s por domínio. Timeout 10s.
5. `compliance_record` criado para cada empresa coletada.

**2b — Sinais comerciais (tabela `opportunity_signal`):**
6. **Detector de vagas**: monitorar LinkedIn Jobs, Indeed, Catho por CNPJ/nome da empresa. Vaga em área-alvo → sinal de tipo `vaga`, confiança 0.8.
7. **Monitoramento de notícias**: RSS + Google News por CNPJ/nome. Detectar: expansão, inauguração, lançamento, mudança de diretoria, rebranding, feiras. Classificar por tipo de sinal.
8. **Atividade em Maps**: avaliações recentes → `atividade_recente`. Avaliações antigas + nota baixa → sinal negativo de liveness.
9. Painel: coleta por ICP, progresso SSE, custo acumulado, sinais encontrados por empresa.

**Critério de aceite:**
```bash
# ICP: CNAE 70.20-4, SP, ME/EPP
# Empresas com CNPJ + site + sócios + evidência em cada campo ✓
# Pelo menos 1 sinal detectado em amostra de 50 empresas ✓
# Custo estimado exibido antes de rodar Places ✓
# pytest tests/hunter/test_companies.py -v ✓
# pytest tests/hunter/test_signals.py -v ✓
```

---

### SPRINT 3 — Resolução de Entidades e Deduplicação

**Meta:** uma empresa = um registro, venha de qualquer fonte.

**Entregáveis:**

1. **Chaves de vínculo, em prioridade:**
   1. CNPJ exato
   2. Domínio do site (normalizado: sem www, sem protocolo)
   3. Telefone normalizado (DDD + número)
   4. Nome + município (pg_trgm ≥ 0.85)
2. **Mesclagem com auditoria**: log reversível por `evidence`.
3. **Vínculo contato → empresa** pelo domínio do email.
4. **Detector de contador**: domínio em > 20 CNPJs diferentes → `email_type = contador`. CNAE grupo 69 → `contador`. Bloqueado para envio, badge no painel.
5. **Classificação de email**:
   - `pessoal`: padrão nome (joao, j.silva)
   - `funcao`: padrão departamental (rh@, comercial@)
   - `generico`: contato@, info@, faleconosco@
   - Mapeamento automático para `department`.
6. `contact_role` inferido por cargo e padrão de email: "Gerente de Marketing" → papel `decisor` para departamento Marketing, confiança 0.6.
7. Exportação "lista bruta consolidada".

**Critério de aceite:**
```bash
# Mesma empresa via CNPJ + Maps + crawler → 1 registro em company ✓
# Sócios vinculados com contact_role = socio ✓
# Contador detectado e bloqueado ✓
# pytest tests/hunter/test_resolution.py -v ✓
```

---

### SPRINT 4 — Filtro: Validação Técnica de Email

**Meta:** saber se o email existe sem enviar nada.

**Entregáveis:**

1. **Pipeline em camadas** (para na primeira reprovação):
   ```
   1. Sintaxe (RFC 5321)
   2. Domínio descartável (lista pública + editável por workspace)
   3. Conta de função → sinaliza email_type=funcao, não reprova
   4. MX existe e resolve
   5. SMTP via Reacher (sem envio)
   6. Catch-all detection (endereço aleatório; aceita → catch-all)
   ```
2. Interface `EmailVerifier`. Implementações: `ReacherVerifier`, `MillionVerifierVerifier`, `ZeroBounceVerifier`.
3. Cache por email (TTL 90 dias), por domínio catch-all (TTL 7 dias).
4. Estados: `valido`, `arriscado` (catch-all ou desconhecido), `invalido`, `bloqueado`.
5. Rate limit: máx. 3 verificações simultâneas por MX.
6. Pré-voo: checar porta 25 acessível. Se não: fallback API + alerta no painel.
7. **Verificação SMTP não é garantia absoluta de existência da caixa.** Exibir aviso no painel.
8. Painel: distribuição de status, reverificação de emails > 90 dias.

**Critério de aceite:**
```bash
# 500 emails: distribuição coerente de estados ✓
# Reacher mockado em testes (sem chamada real) ✓
# Catch-all detectado → todos os emails do domínio → arriscado ✓
# pytest tests/filtro/test_validation.py -v ✓
```

---

### SPRINT 5 — Filtro: Enriquecimento e Scores

**Meta:** todo contato válido com nome, cargo, departamento e quatro scores calculados.

**Entregáveis:**

**5a — Enriquecimento:**
1. Resolução de nome (cascata, para na primeira): assinatura → página de equipe → QSA → local do email → Claude API (custo registrado).
2. Gerador de padrões de email para sócios: nome@, n.sobrenome@, nome.sobrenome@, inicialsobrenome@. Verificados pelo Sprint 4.
3. Validação cruzada: email bate com domínio? Site cita CNPJ? Nome na equipe ou QSA? Cada confirmação aumenta `confianca` na `evidence`.
4. `liveness_score` (0–100): situação ativa +30, HTTPS +20, ano no rodapé +10, blog 90d +15, Maps 180d +15, redes recentes +10.
5. Classificador de setor por IA: CNAE + texto do site → `setor_humano` + `gatilhos_previaveis[]`. Um registro por empresa, custo registrado.

**5b — Inteligência comercial (módulo `intel/`):**
6. CRUD de **Catálogo de Ofertas** (`commercial_offer`): nome, descrição, segmentos, departamentos, problemas que resolve, ticket estimado, cases disponíveis.
7. CRUD de **Necessidades por Departamento** (`department_need`): necessidade, relevância 1–5, ofertas relacionadas.
8. CRUD de **Papéis de Compra** (`contact_role`): decisor, influenciador, usuário, orçamento, aprovador, gatekeeper. Uma pessoa pode ter múltiplos papéis.
9. **Gerador de hipóteses** (`intel/hypothesis/`):
   - Entrada: empresa + contato + department + sinais + necessidades + ofertas disponíveis.
   - Saída: `opportunity_hypothesis` com justificativa em linguagem natural e `opportunity_score`.
   - IA usa **somente** dados com evidência registrada.
   - Sem sinal comercial e sem oferta compatível: hipótese não gerada (contato fica em "sem hipótese").
10. Fila de aprovação de hipóteses no painel antes de qualquer mensagem ser gerada.
11. Painel: top 50 oportunidades por score, evidências clicáveis, oferta sugerida por hipótese.

**Critério de aceite:**
```bash
# 100 contatos válidos: nome preenchido ≥ 70% ✓
# Quatro scores calculados e armazenados independentemente ✓
# Hipótese gerada apenas quando há sinal + oferta compatível ✓
# Hipótese sem evidências suficientes → não gerada (registrado em PROGRESSO) ✓
# pytest tests/filtro/test_enrichment.py -v ✓
# pytest tests/intel/test_hypothesis.py -v ✓
```

---

### SPRINT 6 — Disparo: Infraestrutura e Proteção

**Meta:** enviar com segurança, sem risco ao domínio principal.

**Entregáveis:**

1. Cadastro de caixas de envio: Gmail API, Graph, SMTP genérico.
2. Cadastro de domínios com classificação `principal` / `secundario`.
3. Pré-voo de DNS (`checkdmarc`): SPF, DKIM, DMARC `quarantine/reject`, rDNS. **Envio bloqueado em código se qualquer item falhar.**
4. **`disparo/senders/guard.py`**: cold email bloqueado para domínio `principal`. Não configurável. Lança exceção se tentado.
5. Plano de aquecimento por caixa (rampa configurável). Padrão: 5 / 15 / 30 / 50 envios/dia nas semanas 1–4.
6. Agendador: rodízio de caixas, intervalo aleatório (3–15 min), janela comercial (seg–sex 08–18h no fuso do destinatário).
7. Cabeçalhos obrigatórios: `List-Unsubscribe` RFC 8058, `Message-ID` próprio, versão texto simples.
8. Supressão checada **individualmente** antes de cada mensagem.
9. Processador de bounces: hard → `invalido` bloqueado; soft → 2 tentativas, depois `arriscado`.
10. Disjuntor (`circuit_breaker.py`): pausa se bounce > 3% / 24h, reclamação, erro de autenticação. Alerta no painel + email ao admin.
11. Em dev: **100% Mailpit.** Nenhum byte real sai.

**Decisões necessárias antes deste sprint:**
- Domínios secundários: quais comprar?
- Provedor de caixas: Google Workspace, Microsoft 365 ou SMTP?
- Reacher: licença AGPL (interno) ou comercial (SaaS)?

**Critério de aceite:**
```bash
# 200 envios simulados no Mailpit ✓
# Limites de caixa e janela respeitados ✓
# Supressão checada em cada envio ✓
# guard.py bloqueia domínio principal com exceção ✓
# Disjuntor dispara com bounce simulado > 3% ✓
# pytest tests/disparo/test_senders.py -v ✓
```

---

### SPRINT 7 — Disparo: Composição e Aprovação

**Meta:** mensagens únicas por hipótese, revisadas antes de sair.

**Entregáveis:**

1. Templates com variáveis: `{{primeiro_nome}}`, `{{empresa}}`, `{{cidade}}`, `{{setor}}`, `{{departamento}}`, `{{sinal}}`, `{{oferta}}`, `{{case}}`. Blocos condicionais por departamento ou setor.
2. **Objetivos de contato separados** (três camadas progressivas):
   - Sonda: confirmar pessoa e pertinência
   - Seguimento: identificar abertura comercial
   - Último toque: proposta direta com CTA
3. **Personalização por IA** — estrutura obrigatória:
   ```
   1. Motivo específico (referência real da empresa, com evidência)
   2. Contexto da empresa (sinal comercial, se houver)
   3. Problema ou oportunidade identificada
   4. Oferta compatível (do catálogo)
   5. Prova ou case real (se cadastrado)
   6. CTA único e objetivo
   ```
   - **IA usa somente `evidence` registrada.** Zero fatos inventados.
   - Sem hipótese aprovada → mensagem não gerada.
   - Sem evidências suficientes → template-sonda genérico seguro.
   - Limite: 150 palavras no primeiro toque.
4. Variação natural: 3 versões por pessoa, maior diversidade léxica selecionada.
5. Checagem de conteúdo: Rspamd < 5, lista de spam PT-BR, máx. 1 link, zero anexos, zero imagens, variável vazia → bloqueia, maiúsculas > 30% → alerta.
6. Fila de aprovação em lote: preview, editar inline, aprovar/descartar. Filtro por ICP e status.
7. Biblioteca de sondas por departamento (editável).

**Critério de aceite:**
```bash
# 50 mensagens para 2 ICPs: Rspamd < 5 em todos ✓
# Sem hipótese aprovada → mensagem não gerada ✓
# Variável vazia → bloqueada na fila ✓
# pytest tests/disparo/test_compose.py -v ✓
```

---

### SPRINT 8 — Disparo: Sequências, Respostas e Integração CRM

**Meta:** ciclo completo — sonda → resposta → oportunidade no CRM externo.

**Entregáveis:**

1. Sequências configuráveis (padrão: dia 0 / dia 4 / dia 10). Mesmo fio (`In-Reply-To` + `References`).
2. Regras de parada: resposta, bounce hard, descadastro, reunião marcada, outra pessoa da empresa respondeu.
3. Leitor de caixas: detecta respostas por `Message-ID`, ausências, bounces.
4. Classificação de respostas por IA + ações:

   | Classificação | Ação |
   |---|---|
   | interessado | webhook CRM + task "WhatsApp 24h" + deal interno |
   | nao_agora | reagenda 90 dias |
   | pessoa_errada + indicação | novo contato + vincula empresa |
   | ausente | pausa até data extraída do texto |
   | descadastro / irritado | suppression + compliance_record + sequência encerrada |
   | objecao | outreach_feedback + tag + move para Nutrição |

5. **Integração CRM externo:**
   - Webhook POST configurável por workspace para: deal criado, stage changed, ganho/perdido.
   - Deduplicação por `crm_externo_id` antes de cada envio.
   - Log de cada webhook: status HTTP, payload, retentativas.
   - **Não sincroniza automaticamente cada contato pesquisado** — apenas deals relevantes.
6. Painel interno mínimo (complementar ao CRM externo): deals + tasks + histórico por contato e empresa.
7. `outreach_feedback`: objeções, aprendizados, registrado por classificação de resposta.
8. Exportação CSV do funil.

**Critério de aceite:**
```bash
# Mailpit: sonda → resposta "interessado" → webhook CRM disparado ✓
# "não agora" → reagendado 90 dias ✓
# Descadastro → suppression + compliance_record ✓
# Ausente → sequência pausada até data extraída ✓
# Webhook com payload correto + log de status ✓
# pytest tests/disparo/test_sequences.py -v ✓
# pytest tests/disparo/test_crm_integration.py -v ✓
```

---

### SPRINT 9 — Métricas e Aprendizado por Segmento

**Meta:** saber o que funciona, com qual segmento, com qual oferta.

**Entregáveis:**

1. **Painel de métricas** por campanha / ICP / departamento / caixa / template / oferta:
   - Aquisição: empresas encontradas, qualificadas, contatos válidos, custo/contato.
   - Abordagem: mensagens aprovadas, enviadas, bounces, respostas, descadastros, reclamações.
   - Conversão: respostas positivas, reuniões, oportunidades, propostas, ganhos, receita, custo/reunião.
   - Inteligência: segmentos com melhor resposta, departamentos mais interessados, ofertas mais convertidas, objeções frequentes, motivos de perda.
2. **Modo teste por segmento**:
   - 2 ICPs a comparar, amostra mínima de 40 por ICP.
   - Critério de corte definido antes: "reunião em 21 dias".
   - Veredito automático ao completar o ciclo.
3. **Comparação A/B** de assunto e abertura da mensagem.
4. **Relatório semanal automático** ao admin: performance vs semana anterior.
5. **Abertura de email não exibida como métrica principal.** Exibida com aviso de baixa confiabilidade.

**Critério de aceite:**
```bash
# Seed de dados simulados → painel completo com as 4 dimensões ✓
# Modo teste: veredito correto com dados de amostra ✓
# pytest tests/metricas/ -v ✓
```

---

### SPRINT 10 — Produção, Robustez e Multi-workspace

**Meta:** rodar em servidor real, para múltiplos workspaces, sem intervenção manual.

**Entregáveis:**

1. **`docs/DEPLOY.md`**: escolha de provedor (Hetzner, Vultr — liberam porta 25 sob pedido), pedido de liberação passo a passo, DNS reverso, Nginx + SSL Let's Encrypt, Docker Compose em produção.
2. Backup diário: `pg_dump` automático, 30 dias de retenção, upload S3-compatible.
3. Monitoramento de jobs presos: job sem `finish_job` em 2× tempo médio → alerta.
4. Rotação de logs: 7 dias de app, 30 dias de envio.
5. Limites por workspace: contatos (10.000), envios/dia (500), gasto API/mês (U$50).
6. Papéis de usuário: Admin, Operador (coleta + envio), Aprovador (só fila).
7. Renovação automática de tokens OAuth com retry e alerta.
8. Teste de carga: 50k empresas, 20k contatos, 10 caixas. Meta: filtro em < 30 min.
9. **`docs/OPERACAO.md`**: domínio secundário, aquecimento de caixa, ICP do zero, interpretar painel, integrar CRM.
10. Segundo workspace de setor diferente (ex: saúde ou construção): ciclo completo sem tocar em código.

**Critério de aceite:**
```bash
# Segundo workspace: ciclo Hunter → Filtro → Intel → Disparo ✓
# pytest tests/ -v --tb=short → zero falhas ✓
# Teste de carga: 20k contatos filtrados em < 30 min ✓
```

---

## SEÇÃO 3 — RISCOS E DECISÕES ABERTAS

### Riscos técnicos

| Risco | Impacto | Mitigação |
|---|---|---|
| Porta 25 bloqueada | Sem verificação SMTP própria | VPS que libere (Hetzner, Vultr); fallback API paga |
| Base CNPJ dezenas de GB | Disco e tempo | Filtrar por UF/CNAE do ICP; staging separado |
| Google Places custo por chamada | Custo cresce rápido | Máscara mínima, cache 30d, estimativa antes de rodar |
| Catch-all dominante no Brasil | Muitos "arriscados" | Volume baixo; bounce ou resposta decide |
| IA inventando fatos | Credibilidade | Só evidence registrada + aprovação humana |
| Domínio principal queimado | Email do dia a dia | Bloqueio em código, não configuração |
| CRM externo indisponível | Oportunidade não sincronizada | Queue com retry; log de falhas; painel interno como fallback |
| Hipóteses falsas positivas | Abordagem sem base real | Fila de aprovação humana de hipóteses antes das mensagens |

### Decisões pendentes antes do Sprint 6

1. Domínios secundários: quais comprar? Quantas caixas por domínio?
2. Provedor de caixas: Google Workspace, Microsoft 365 ou SMTP próprio?
3. Maps: Google Places, Apify ou paralelo?
4. Reacher: AGPL (interno) ou comercial (SaaS)?
5. CRM externo: qual sistema? Qual endpoint de webhook? Quais campos mapear?
6. Prospector como produto SaaS? Define cobrança por workspace no Sprint 10.

---

## SEÇÃO 4 — FASES DE ENTREGA

```
FASE 1 — ENCONTRAR E QUALIFICAR (Sprints 0–5)
Resultado: base confiável de empresas, contatos, sinais e hipóteses aprovadas.

FASE 2 — ABORDAR E CONVERTER (Sprints 6–8)
Resultado: mensagens contextualizadas, abordagens controladas, oportunidades no CRM.

FASE 3 — MEDIR E ESCALAR (Sprints 9–10)
Resultado: indicadores comerciais, aprendizado por segmento, infraestrutura de produção.

PILOTO COMERCIAL: realizar antes de expandir volumes.
Meta de sucesso: oportunidades geradas, reuniões realizadas, negócios ganhos.
Não usar volume de envio como prova de sucesso.
```

---

## SEÇÃO 5 — REFERÊNCIAS TÉCNICAS

| Tema | Fonte |
|---|---|
| Verificação de email | [reacherhq/check-if-email-exists](https://github.com/reacherhq/check-if-email-exists) |
| Dados CNPJ | [cnpj-data-pipeline](https://www.tabnews.com.br/caiopizzol/open-source-cnpj-data-pipeline-50-m-de-cnpjs-no-seu-postgresql-sem-dor-de-cabeca) · [rictom/cnpj-sqlite](https://github.com/rictom/cnpj-sqlite) |
| QSA de sócios | [turicas/socios-brasil](https://github.com/turicas/socios-brasil) |
| Requisitos Gmail/Yahoo 2026 | [Bird deliverability guide](https://bird.com/docs/knowledge-base/deliverability/gmail-yahoo-requirements) |
| DNS / DMARC | [checkdmarc (pip)](https://pypi.org/project/checkdmarc/) |
| Consulta CNPJ pontual | [BrasilAPI](https://brasilapi.com.br/docs#tag/CNPJ) |
| Extração de assinatura | [Nylas docs](https://developer.nylas.com/docs/email/signature-extraction/) |
| Microsoft Basic Auth | [heise.de](https://heise.de/-9767989) |

---

*Agência VIA — sistema interno de inteligência comercial e prospecção B2B. Versão 3.0 — Novembro 2026.*
