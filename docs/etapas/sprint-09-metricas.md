# Etapa 9 — Métricas e aprendizado

**Em uma frase:** saber o que funciona, com qual segmento e qual oferta.

## O que você terá no final
- Painel com 4 visões: aquisição, abordagem, conversão e inteligência.
- Teste entre 2 ICPs (mínimo 40 por ICP; critério "reunião em 21 dias") com veredito automático.
- Comparação A/B de assunto e abertura.
- Relatório semanal ao admin.
- Taxa de abertura exibida só com aviso de baixa confiabilidade.

## Ferramentas
`dataviz`, `data:build-dashboard`, `data:statistical-analysis`.

## Antes de começar
Etapa 8 concluída; dados de piloto ou simulados.

## Como conferir que deu certo
```bash
pytest tests/metricas/ -v
```
Dados simulados → painel completo e veredito correto no modo teste.
