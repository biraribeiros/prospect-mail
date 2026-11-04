# Etapa 2b — Hunter: sinais comerciais

**Em uma frase:** descobre "momentos de compra": vagas abertas, expansões, eventos e notícias.

## O que você terá no final
- Tabela de sinais por empresa (tipo, fonte, link, confiança, validade).
- Painel mostrando os sinais encontrados por empresa.

## O que será feito
1. Tabela `opportunity_signal`.
2. Detector de vagas **atrás de adaptador** (F11): começar por RSS e páginas "trabalhe conosco"; LinkedIn/Indeed/Catho só via fornecedor licenciado.
3. Monitor de notícias (RSS/Google News): expansão, inauguração, lançamento, mudança de diretoria, feiras.
4. Atividade recente no Maps (avaliações) como sinal positivo/negativo.
5. Progresso em tempo real (SSE) e custo acumulado.

## Ferramentas
`context7`, `brightdata-plugin:search` / `data-feeds` (opcionais).

## Antes de começar
Decidir a **fonte de vagas** e o orçamento (F11). Etapa 2a concluída.

## Como conferir que deu certo
```bash
pytest tests/hunter/test_signals.py -v
```
Amostra de 50 empresas → pelo menos 1 sinal detectado, com fonte e link.

## Atenção
Sinal sem fonte verificável não é gravado.
