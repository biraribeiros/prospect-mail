# Progresso

Leia também: [VALIDACAO_DO_PLANO.md](VALIDACAO_DO_PLANO.md) (correções aprovadas, já no Prompt Mestre v3.1).

| Etapa | Status | Branch | Observações |
|---|---|---|---|
| Planejamento e validação | ✅ feito | main | Correções F1–F17 aprovadas (Prompt Mestre v3.1) |
| 0 Fundação | ✅ feita e unida | `sprint-00-fundacao` (PR #1) | 66 testes; aceite conferido |
| 1 Caixas de email | ✅ feita e unida | `sprint-01-caixas-email` (PR #2) | 149 testes; aceite ponta a ponta conferido |
| 2a Empresas | ✅ feita (aguarda PR) | `sprint-02a-empresas` | 225 testes no total (76 novos); aceite ponta a ponta conferido (ver abaixo) |
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

## Etapa 2a — o que foi feito (Hunter: empresas)
- Migração `0003`: tabela `api_cache` (cache de 30 dias das buscas pagas) e novas colunas em `company` (telefone, endereço, situação da leitura do site).
- **Base CNPJ da Receita:** lê os arquivos de `data/cnpj/` (.zip ou extraídos), filtra pelo perfil (CNAE, UF, cidade, porte, só matrizes ativas) em 3 passadas, sem carregar a base inteira no banco. Empresas viram `company`; sócios e administradores (pessoas físicas) viram `contact` com `contact_role`. O email cadastral da Receita fica só como evidência de baixa confiança (30%).
- **Google Places (New):** Text Search com máscara mínima de campos, paginação, cache de 30 dias, **estimativa de custo antes de rodar** com confirmação, e limite de gasto por execução (`PLACES_MAX_USD_PER_RUN`). Empresas já conhecidas são reconhecidas pelo `place_id` ou pelo domínio.
- **Leitor de sites:** home + páginas de contato/sobre/equipe; respeita `robots.txt` e `Crawl-delay`, 1 requisição/s por domínio, timeout de 10 s, limite de 2 MB por página; extrai emails (inclusive `[at]`, `&#64;` e Cloudflare), telefones, WhatsApp, CNPJ (só válidos) e redes sociais. Sites que dependem de JavaScript ficam marcados como "precisa JS".
- **Proteção contra SSRF** (`app/core/netsafe.py`): só http/https nas portas 80/443, só endereços públicos, conferido em cada redirecionamento.
- Telas: Hunter → Empresas (filtros, origem de cada dado, página da empresa com todas as evidências) e Coleta (3 fontes, estimativa de custo, progresso em tempo real por SSE, coletas recentes). Links vindos de dados externos só são clicáveis se forem http(s).
- Tudo com evidência (fonte, link, confiança) e registro de conformidade; jobs idempotentes, com custo registrado.

### Como validar a Etapa 2a
```bash
docker compose run --rm app pytest tests/hunter/ -v
docker compose exec app python scripts/verificar_etapa2a.py   # aceite ponta a ponta (arquivos fictícios; limpa no final)
```
Resultado (05/11/2026): ICP "CNAE 70.20-4, SP, ME/EPP" → 2 empresas com CNPJ, razão social e evidência em cada campo, 3 sócios como contatos, conformidade para cada registro, progresso em tempo real, estimativa de custo exibida; sem chave do Google a busca paga não roda; reimportar não duplica.

### Decisões e pendências da Etapa 2a
- Fonte de Maps: **só Google Places** (decisão do dono). Apify fica para depois, se o custo pedir.
- **Para usar de verdade:** (1) chave em `GOOGLE_PLACES_API_KEY` no `.env`; (2) arquivos *Empresas*, *Estabelecimentos*, *Sócios* e *Municípios* da base CNPJ em `data/cnpj/` (dezenas de GB descompactados). Os preços do Places no `.env` são estimativas: conferir a tabela atual do Google.
- Não foi testada a leitura real de um site público nem a chamada real ao Google (testes com respostas simuladas, sem gastar). Fazer um teste curto com poucos sites e 1 consulta antes de uso em escala.
- Filtro de porte **MEI** não é aplicado (a Receita guarda isso em outro arquivo, Simples); o painel avisa.
- Playwright não foi incluído: sites que exigem JavaScript só são marcados. Reavaliar se for muitos.
- Limite conhecido do SSRF: entre a checagem do DNS e a conexão, o DNS poderia mudar (rebinding). Para fechar, conectar direto no IP validado (Etapa 10); a mesma checagem deve ser aplicada ao IMAP.
- Empresas repetidas entre fontes (CNPJ × Maps × site) ainda não são juntadas: é a Etapa 3.
- Revisão de segurança desta etapa feita lendo o código (SSRF do crawler, links vindos de dados externos, autorização por workspace, caminhos de arquivo, chave da API); nenhuma falha de alta confiança. A skill `security-review` não foi rodada nesta etapa.
- `app/core/csvio.py` ainda importa UF/porte de `app/web/forms.py` (acoplamento antigo, anotado na Etapa 0).

## Etapa 1 — o que foi feito (Hunter: caixas de email)
- Migração `0002`: tabelas `mailbox_account` (config cifrada em `tokens_enc`) e `mail_interaction` (só metadados: data, direção, palavras-chave); novas colunas em `contact` (telefone, site, trecho de assinatura, tipo de domínio, contagens, última interação).
- Interface única `MailboxReader` com 4 leitores: **mbox/eml** e **IMAP** (testados), **Gmail API** e **Microsoft Graph** (testados só com respostas simuladas; renovação de token OAuth pronta).
- Varredura de Recebidos e Enviados; filtro de ruído editável por workspace (robôs, bancos, redes sociais, marketplaces, cabeçalhos de lista); leitura de assinatura por regras; reserva por IA (`claude-haiku-5-5`, chamada direta por httpx, custo estimado no `job_run`; desligada sem `ANTHROPIC_API_KEY`).
- Nota de relacionamento 0–100 (volume, mão dupla, recência, palavras-chave); domínio corporativo × pessoal.
- Telas: Hunter → Caixas (enviar .mbox, conectar IMAP, varrer, remover), Contatos (filtros, ordenação por nota, CSV), Filtro de ruído. A varredura roda no worker.
- Garantias testadas: 1.000 mensagens → contatos sem duplicados; reprocessar → zero registros novos; registro de conformidade e evidência por contato; **corpo e assunto do email nunca são gravados**.

### Como validar a Etapa 1
```bash
docker compose run --rm app pytest tests/hunter/test_mailbox.py -v
docker compose exec app python scripts/verificar_etapa1.py   # aceite ponta a ponta com o worker
```
Resultado (05/11/2026): 1.000 mensagens fictícias lidas em ~13 s → 270 contatos, 900 interações, 100 ruídos ignorados; reprocessar não alterou nenhuma contagem.

### Decisões e pendências da Etapa 1
- Teste real só com `.mbox` (decisão do dono). IMAP: validado com servidor simulado. **Gmail/Outlook**: leitores prontos, mas sem tela de conexão OAuth; ligar quando houver credenciais (Google Cloud "Interno" / Microsoft).
- A skill `security-review` foi aplicada; nenhuma falha de alta confiança. Nota de baixa confiança: o bloqueio de IPs internos no IMAP só vê endereços literais; um nome DNS que aponte para rede interna passaria. Resolver o DNS antes de conectar (Etapa 10).
- Preços de IA em `.env` (`LLM_PRICE_*`) são estimativas configuráveis; conferir os preços atuais antes de usar em produção.
- Conferência visual das telas novas com login não foi feita no navegador (leitura da senha local foi bloqueada); cobertas por testes de rota e pelo aceite ponta a ponta.
- Arquivos `.mbox` enviados ficam em `data/mbox/<workspace>/` (fora do Git).

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
