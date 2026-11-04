# Etapa 7 — Mensagens e aprovação

**Em uma frase:** a IA escreve emails únicos, baseados em fatos comprovados, e você aprova em lote.

## O que você terá no final
- Modelos com variáveis e três objetivos: sonda → seguimento → último toque.
- IA escrevendo no formato: motivo → contexto → oportunidade → oferta → prova → um único pedido.
- Fila de aprovação em lote (ver, editar, aprovar, descartar).
- Verificação anti-spam (Rspamd < 5, 1 link, sem imagem/anexo, até 150 palavras).

## O que será feito
1. Modelos e biblioteca de sondas por departamento.
2. Geração com 3 variações; escolhe a mais diversa.
3. Regras de bloqueio: variável vazia, MAIÚSCULAS em excesso, sem hipótese aprovada.
4. Tela de aprovação em lote.

## Ferramentas
`claude-api`, `anthropic-skills:copywriter`, `marketing:email-sequence`, `design:ux-copy`.

## Antes de começar
Etapas 5b e 6b concluídas; ter textos e cases reais da VIA.

## Como conferir que deu certo
```bash
pytest tests/disparo/test_compose.py -v
```
- 50 mensagens em 2 ICPs, todas com Rspamd < 5.
- Sem hipótese aprovada → mensagem não é gerada.
- Variável vazia → bloqueada na fila.

## Atenção
Nenhum fato fora das evidências registradas.
