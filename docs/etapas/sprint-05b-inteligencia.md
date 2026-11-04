# Etapa 5b — Inteligência comercial

**Em uma frase:** responde "por que abordar esta empresa, esta pessoa, agora?" — com provas.

## O que você terá no final
- Catálogo de ofertas da VIA, necessidades por departamento e papéis de compra.
- Hipóteses de oportunidade com justificativa e nota.
- Fila onde **você aprova cada hipótese** antes de qualquer mensagem existir.
- Top 50 oportunidades com evidências clicáveis.

## O que será feito
1. Telas de cadastro: ofertas, necessidades, papéis de compra.
2. Gerador de hipóteses: usa **somente** dados com evidência registrada.
3. Regra: sem sinal e sem oferta compatível → **não gera** (contato fica "sem hipótese").
4. Fila de aprovação e painel de oportunidades.

## Ferramentas
`claude-api`, `anthropic-skills:via-context` (ofertas reais da VIA), `anthropic-skills:sales-enablement` (cases), `design:ux-copy`.

## Antes de começar
Você precisa ter à mão: lista de serviços da VIA, faixa de preço e cases reais.

## Como conferir que deu certo
```bash
pytest tests/intel/test_hypothesis.py -v
```
Hipótese só aparece quando há sinal + oferta compatível + evidência.

## Atenção
Sem aprovação humana da hipótese, a Etapa 7 não escreve mensagem.
