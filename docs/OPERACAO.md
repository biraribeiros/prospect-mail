# Operação do dia a dia

Tudo que você precisa saber para manter o Prospector saudável. A página **Operação** (menu Configurações → Operação, só administradores) mostra o estado de cada item abaixo.

## Rotina sugerida
| Quando | O que fazer |
|---|---|
| **Todo dia** (5 min) | Abrir **Disparo**: conferir se não há faixa vermelha (“Envio PAUSADO”), olhar bounces e respostas; aprovar hipóteses e mensagens pendentes. |
| **Toda segunda** | Ler o resumo semanal (chega por email aos administradores; também em Métricas → “Ver resumo semanal”). |
| **Todo mês** | Atualizar o servidor, conferir o espaço em disco (`df -h`) e a conta das APIs (Google, Anthropic, verificador). |
| **A cada novo domínio de envio** | Conferir o DNS (Disparo → “Conferir DNS”) e começar o aquecimento do zero. |

## O que o sistema faz sozinho
| Rotina | Frequência | Onde ver |
|---|---|---|
| Agendar e enviar mensagens aprovadas | a cada minuto | Disparo |
| Ler respostas e bounces das caixas de envio (se IMAP configurado) | a cada 10 min | Disparo → Respostas |
| Encerrar tarefas presas e avisar os administradores | a cada 15 min | Operação |
| Renovar tokens Gmail/Microsoft e marcar caixas que precisam de nova autorização | todo dia, 3h | Operação |
| **Backup do banco** (compactado) | todo dia | Operação e pasta `data/backups/` |
| Remover backups com mais de 30 dias | todo dia | — |
| Resumo semanal por email | segunda, 8h | Métricas |

## Alertas que você pode receber por email
- **Envio pausado pelo disjuntor** (mais de 3% de bounces em 24 h). O que fazer: não libere logo. Veja em Filtro se há muitos emails “arriscados” sendo enviados, confira a lista usada e só então clique em **Liberar envio** (Disparo).
- **Tarefas presas encerradas**: o worker caiu no meio. Basta rodar a coleta de novo.
- **Caixa precisa de nova autorização**: o Google/Microsoft revogou o acesso; conecte a caixa de novo.

## Backups
Os backups ficam em `data/backups/prospector-AAAAMMDD-HHMMSS.sql.gz` (30 dias). **Copie essa pasta para fora do servidor** (semanalmente, por exemplo com `rsync` ou o painel do seu provedor), senão um defeito no disco leva tudo junto.

Backup manual (antes de atualizar):
```bash
docker compose exec -T postgres pg_dump -U prospector prospector | gzip > data/backups/manual-$(date +%Y%m%d).sql.gz
```
Restaurar (**apaga o banco atual**):
```bash
docker compose stop app worker
docker compose run --rm -v ./data/backups:/backups -v ./docker:/app:ro --entrypoint sh backup /app/restaurar.sh /backups/ARQUIVO.sql.gz
docker compose start app worker
```
Faça este teste de restauração **uma vez** antes de usar de verdade.

## Logs
Cada serviço guarda no máximo 5 arquivos de 10 MB (rotação automática). Para ver: `docker compose logs -f app` (ou `worker`).

## Limites por workspace
Em **Operação**: máximo de contatos, de envios por dia e de gasto mensal com APIs (0 = sem limite). Ao atingir o gasto, coletas pagas e IA param sozinhas com uma mensagem clara; ao atingir os envios do dia, o resto fica agendado para o dia seguinte.

## Pessoas e papéis
| Papel | Pode |
|---|---|
| **Admin** | Tudo, inclusive limites, pessoas, liberar o disjuntor e criar workspaces. |
| **Operador** | Coletar, cadastrar perfis/ofertas/caixas, escrever e enviar. Não aprova hipóteses nem mensagens. |
| **Aprovador** | Aprovar/rejeitar hipóteses e mensagens. Não altera cadastros. |

## Novo workspace (outro setor)
Operação → **Novo workspace**. Ele nasce vazio e isolado: cadastre perfis (ICP), departamentos e ofertas do novo setor (Inteligência → Ofertas → “Carregar modelos” ajuda a começar). Nenhuma mudança de código é necessária.

## Capacidade medida (teste de carga)
Resultados do script `scripts/teste_carga.py` (ambiente de desenvolvimento, resumo em `docs/CARGA.md`). O gargalo **não** é o sistema: é o ritmo de envio SMTP por caixa — por segurança, no máximo 50 envios/dia por caixa (após o aquecimento) com 3 a 15 minutos entre eles. Para enviar mais, **adicione caixas e domínios**, nunca acelere uma caixa.

## Quando algo dá errado
| Sintoma | Causa provável | Solução |
|---|---|---|
| Painel não abre | contêiner parado | `docker compose ps` e `docker compose up -d` |
| “A fila de tarefas está indisponível” | Redis fora do ar | `docker compose restart redis worker` |
| Mensagens não saem | envio pausado, DNS pendente, caixa pausada, fora do horário ou limite do dia | Disparo mostra cada motivo |
| Todas as caixas “bloqueada” com “domínio principal” | o domínio é o mesmo lido pelo Hunter | use um domínio secundário |
| Coleta parou com “Limite … atingido” | limite do workspace | ajuste em Operação |
| Senhas das caixas não abrem após reinstalar | `FERNET_KEYS` diferente | restaure a chave original do `.env` |
