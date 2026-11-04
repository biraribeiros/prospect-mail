"""Verificação de ponta a ponta contra o sistema em execução (critério de aceite da Etapa 0).

Uso: docker compose exec app python scripts/verificar_aceite.py
Usa ADMIN_EMAIL / ADMIN_PASSWORD do .env. Não imprime a senha.
"""
import os
import re
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.environ.get("BASE_URL", "http://localhost:8000")
c = httpx.Client(base_url=BASE, follow_redirects=False)


def tok(path):
    return re.search(r'name="csrf_token" value="([^"]+)"', c.get(path).text).group(1)


def check(ok, msg):
    print(("OK   " if ok else "FALHA"), msg)
    if not ok:
        sys.exit(1)


r = c.post("/login", data={"email": os.environ["ADMIN_EMAIL"], "senha": os.environ["ADMIN_PASSWORD"],
                           "csrf_token": tok("/login")})
check(r.status_code == 303, "login do administrador")
check("Agência VIA" in c.get("/").text, "workspace Agência VIA aberto")
for slug in ("hunter", "filtro", "inteligencia", "disparo"):
    check("Em construção" in c.get(f"/{slug}").text, f"seção {slug} 'em construção'")

t = tok("/configuracoes/icp/novo")
nome = "ICP Contabilidade MG (aceite)"
if nome not in c.get("/configuracoes/icp").text:
    r = c.post("/configuracoes/icp", data={"nome": nome, "cnaes": "6920-6", "ufs": "MG", "portes": "ME, EPP",
                                           "csrf_token": t})
    check(r.status_code == 303, "criar ICP pelo painel")
check(nome in c.get("/configuracoes/icp").text, "ICP aparece na lista")

with open("samples/contatos_100.csv", "rb") as f:
    r = c.post("/configuracoes/importar/contatos", data={"csrf_token": tok("/configuracoes/importar")},
               files={"file": ("contatos_100.csv", f.read(), "text/csv")})
check(r.status_code == 200, "importar 100 contatos")
print("     ->", "já importado antes" if "já foi importado" in r.text else "importação nova concluída")

from sqlalchemy import func, select  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.core.models import ComplianceRecord, Contact, Evidence  # noqa: E402

with SessionLocal() as s:
    n_contatos = s.scalar(select(func.count()).select_from(Contact).where(Contact.origem == "importacao_manual"))
    n_ev = s.scalar(select(func.count()).select_from(Evidence).where(
        Evidence.entidade_tipo == "contact", Evidence.campo == "email", Evidence.fonte == "importacao_manual"))
    n_comp = s.scalar(select(func.count()).select_from(ComplianceRecord).where(
        ComplianceRecord.entidade_tipo == "contact", ComplianceRecord.origem_dado == "importacao_manual"))
check(n_contatos == 100, f"100 contatos no banco ({n_contatos})")
check(n_ev == 100, f"evidência 'importacao_manual' para cada contato ({n_ev})")
check(n_comp == 100, f"registro de conformidade para cada contato ({n_comp})")
print("Critério de aceite da Etapa 0: tudo certo.")
