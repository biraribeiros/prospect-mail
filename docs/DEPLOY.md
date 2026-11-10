# Como colocar o Prospector no ar (produção)

Passo a passo para quem não é de tecnologia. Tempo estimado: 1 hora. Você precisa de **um servidor** (VPS) com Linux. A hospedagem compartilhada (cPanel/Plesk) **não serve** para rodar o Prospector — ela continua servindo para criar as **caixas de email de envio** dos domínios secundários.

> Enquanto não houver servidor, o Prospector funciona normalmente no seu computador (modo seguro: nenhum email real sai).

## 1. O que contratar
| Item | Sugestão | Observação |
|---|---|---|
| Servidor (VPS) | 2 vCPU, 4 GB de RAM, 40 GB de disco | Qualquer provedor (Locaweb, Hostinger, DigitalOcean, Contabo…). Escolha Ubuntu 22.04 ou 24.04. |
| Domínio do painel | ex.: `prospector.suaagencia.com.br` | Crie um registro **A** apontando para o IP do servidor. |
| Domínios de envio | 1 a 3 domínios **secundários** | Nunca o domínio principal da agência (o sistema bloqueia). |
| Porta 25 | Só se for usar o Reacher | Muitos provedores bloqueiam. Sem ela, use MillionVerifier ou ZeroBounce (API paga). |

## 2. Preparar o servidor (uma vez)
```bash
# Entre no servidor por SSH e rode:
curl -fsSL https://get.docker.com | sh
git clone https://github.com/biraribeiros/prospect-mail.git
cd prospect-mail
cp .env.example .env
```

## 3. Preencher o arquivo `.env`
Abra com `nano .env` e ajuste **apenas** estes campos:
| Campo | O que colocar |
|---|---|
| `APP_ENV` | `prod` |
| `DRY_RUN` | **deixe `true` até concluir a lista de verificação abaixo**; só então `false` |
| `SECRET_KEY` | gere com `python3 -c "import secrets;print(secrets.token_urlsafe(48))"` |
| `FERNET_KEYS` | gere com `python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"` — **guarde uma cópia em local seguro: sem ela as senhas das caixas não abrem** |
| `POSTGRES_PASSWORD` | uma senha forte |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | seu acesso (a senha só é usada na primeira subida) |
| `DOMINIO` | o domínio do painel |
| `PUBLIC_BASE_URL` | `https://` + o domínio do painel (vai nos links de descadastro) |
| `VERIFIER_PROVIDER` / `VERIFIER_API_KEY` | `millionverifier` ou `zerobounce` + a chave |
| `ANTHROPIC_API_KEY`, `GOOGLE_PLACES_API_KEY` | as chaves de API (opcionais no modo manual: campanha escrita por você, sem IA) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | **um SMTP real para o email interno** (alertas do disjuntor, resumo semanal, cartão no Trello). Sem isso, esses avisos vão para o Mailpit e ninguém recebe. Use `starttls` (porta 587) ou `ssl` (465) |
| `TRELLO_BOARD_EMAIL` | endereço secreto "Email para o quadro" do Trello (cada resposta "interessado" vira um cartão). Veja `docs/CAMPANHAS.md` |

## 4. Subir
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose ps          # todos "running"/"healthy"
```
Abra `https://seu-dominio` e entre. O HTTPS é emitido sozinho (Caddy).

## 5. Lista de verificação antes de enviar de verdade
Marque tudo antes de trocar `DRY_RUN=false`:
- [ ] Painel abre em HTTPS e o login funciona.
- [ ] **Operação** mostra “Backup diário ✔” (o primeiro aparece em até 1 minuto após subir).
- [ ] Domínio de envio **secundário** cadastrado e **DNS conferido** (SPF, DKIM, DMARC com `quarantine`/`reject`, DNS reverso — tudo ✔ em Disparo).
- [ ] Caixa de envio criada (cPanel/Plesk/Google/Microsoft) e **aquecimento** respeitado (5 / 15 / 30 / 50 por dia nas semanas 1 a 4).
- [ ] Envio de teste para você mesmo: chegou na caixa de entrada (não no spam) e o link de descadastro funciona.
- [ ] Verificador de emails configurado (Filtro mostra o nome do verificador, sem aviso vermelho) **ou** lista conferida por você e liberada manualmente (Filtro → Liberar manualmente).
- [ ] Email interno testado: provoque um alerta (ou envie o resumo semanal) e confirme que chegou na sua caixa; confira que o cartão chega ao quadro do Trello.
- [ ] Limites definidos em **Operação** (contatos, envios por dia, gasto mensal).
- [ ] `FERNET_KEYS` e o arquivo `.env` guardados fora do servidor.
- [ ] Um backup foi **restaurado em teste** (ver `docs/OPERACAO.md`).

Só então: `DRY_RUN=false` no `.env` e `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`.

## 6. Atualizar para uma versão nova
```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```
O banco é atualizado sozinho (migrações). Faça um backup manual antes (ver `docs/OPERACAO.md`).

## 7. Segurança (resumo)
- Só as portas 80 e 443 ficam abertas; o banco e o Redis não têm porta pública.
- `SESSION_HTTPS_ONLY` é ligado em produção; senhas usam argon2; senhas das caixas são cifradas (`FERNET_KEYS`).
- Feche a porta 22 para a internet ou use chave SSH (sem senha).
- Mantenha o servidor atualizado: `apt update && apt upgrade` uma vez por mês.
