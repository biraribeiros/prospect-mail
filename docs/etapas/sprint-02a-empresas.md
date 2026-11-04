# Etapa 2a — Hunter: empresas

**Em uma frase:** monta a lista de empresas do seu público-alvo a partir de fontes públicas.

## O que você terá no final
- Empresas do ICP com CNPJ, site, sócios e a origem de cada dado.
- Painel de coleta com progresso e **custo estimado antes de rodar**.

## O que será feito
1. Importador da base CNPJ (Receita): download por partes, filtro por ICP, sócios (QSA) como contatos.
2. Adaptador Google Places (campos mínimos, cache 30 dias, estimativa de custo).
3. Adaptador Apify Maps (alternativa mais barata).
4. Crawler de sites (home, contato, sobre, equipe, rodapé): respeita `robots.txt`, 1 requisição/s por domínio, timeout 10 s.
5. Registro de conformidade por empresa.

## Ferramentas
`context7` (httpx, Playwright), `graft`, `brightdata-plugin:scrape` (opcional).

## Antes de começar
Decidir: Google Places, Apify ou os dois. Ter espaço em disco para a base CNPJ (dezenas de GB — filtrar por UF/CNAE).

## Como conferir que deu certo
```bash
pytest tests/hunter/test_companies.py -v
```
ICP de teste: CNAE 70.20-4, SP, ME/EPP → empresas com CNPJ + site + sócios + evidência em cada campo.

## Atenção
Nunca rodar Places sem ver a estimativa de custo.
