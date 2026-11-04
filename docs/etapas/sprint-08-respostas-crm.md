# Etapa 8 — Sequências, respostas e CRM

**Em uma frase:** fecha o ciclo: envio → resposta → oportunidade no seu CRM.

## O que você terá no final
- Sequência dia 0 / 4 / 10 na mesma conversa.
- Leitura de respostas, ausências e bounces.
- IA classificando respostas e executando a ação certa.
- Envio de oportunidades ao CRM externo por webhook (sem duplicar).
- Painel mínimo de negócios e tarefas; exportação CSV do funil.

## Ações por resposta
| Resposta | O que acontece |
|---|---|
| Interessado | Webhook ao CRM + tarefa "WhatsApp em 24h" |
| Não agora | Reagenda em 90 dias |
| Pessoa errada | Novo contato ligado à empresa |
| Ausente | Pausa até a data de retorno |
| Descadastro | Supressão imediata + registro de conformidade |
| Objeção | Registra aprendizado e move para Nutrição |

## Ferramentas
`marketing:email-sequence`, `claude-api`, `engineering:testing-strategy`.

## Antes de começar
**Decisão: qual CRM, endpoint do webhook e campos.** Etapa 7 concluída.

## Como conferir que deu certo
```bash
pytest tests/disparo/test_sequences.py tests/disparo/test_crm_integration.py -v
```
No Mailpit: sonda → resposta "interessado" → webhook com payload correto e log de status.
