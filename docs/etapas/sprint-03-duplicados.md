# Etapa 3 — Sem duplicados

**Em uma frase:** uma empresa vira um único registro, não importa de onde veio.

## O que você terá no final
- Mesclagem automática por CNPJ → domínio → telefone → nome+cidade.
- Contador detectado e bloqueado para envio.
- Cada email classificado (pessoal, de função, genérico) e ligado ao departamento.

## O que será feito
1. Regras de vínculo em ordem de prioridade (nome similar com `pg_trgm ≥ 0.85`).
2. Mesclagem com histórico reversível.
3. Contato → empresa pelo domínio do email.
4. Detector de contador (domínio em mais de 20 CNPJs, ou CNAE grupo 69).
5. Papéis de compra inferidos pelo cargo ("Gerente de Marketing" → decisor, confiança 0,6).
6. Exportação "lista bruta consolidada".

## Ferramentas
`engineering:system-design`, `graft`.

## Antes de começar
Etapas 1 e 2a concluídas.

## Como conferir que deu certo
```bash
pytest tests/hunter/test_resolution.py -v
```
- Mesma empresa via CNPJ + Maps + crawler → 1 registro.
- Sócios ligados à empresa.
- Contador marcado e bloqueado.
