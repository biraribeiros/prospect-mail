# Etapa 5a — Filtro: enriquecimento e notas

**Em uma frase:** todo contato válido ganha nome, cargo, departamento e quatro notas separadas.

## O que você terá no final
- Nome preenchido em ≥ 70% dos contatos válidos.
- Quatro notas independentes: empresa, contato, email e oportunidade (+ nota final ponderada).
- Nota de "vida" da empresa (site ativo, blog recente, Maps, redes).
- Setor em linguagem humana e gatilhos previsíveis (IA, custo registrado).

## O que será feito
1. Descoberta de nome em cascata: assinatura → página de equipe → sócios → parte local do email → IA.
2. Padrões de email para sócios (nome@, n.sobrenome@…), verificados pela Etapa 4.
3. Validação cruzada (email × domínio × CNPJ × equipe) que aumenta a confiança.
4. Cálculo e armazenamento das 4 notas e da nota de vida.
5. Classificador de setor por IA, um registro por empresa.

## Ferramentas
`claude-api` (cache + custo), `engineering:testing-strategy`.

## Antes de começar
Etapas 3 e 4 concluídas; chave da API Claude no `.env`.

## Como conferir que deu certo
```bash
pytest tests/filtro/test_enrichment.py -v
```
100 contatos válidos → nome ≥ 70%; notas gravadas separadamente.
