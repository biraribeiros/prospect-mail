"""Aceite da Etapa 1 contra o sistema em execução: envia uma caixa .mbox de 1.000 mensagens FICTÍCIAS pelo painel,
espera o worker varrer, confere contatos/conformidade e reprocessa para provar que nada duplica.

Uso: docker compose exec app python scripts/verificar_etapa1.py
Usa ADMIN_EMAIL / ADMIN_PASSWORD do .env (a senha não é impressa). Dados de teste ficam num workspace temporário.
"""
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, func, select  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.core.models import ComplianceRecord, Contact, Evidence, MailboxAccount, MailInteraction  # noqa: E402
from tests.hunter.helpers import build_1000  # noqa: E402

BASE = os.environ.get("BASE_URL", "http://localhost:8000")
c = httpx.Client(base_url=BASE, follow_redirects=False, timeout=60)


def tok(path):
    return re.search(r'name="csrf_token" value="([^"]+)"', c.get(path).text).group(1)


def check(ok, msg):
    print(("OK   " if ok else "FALHA"), msg)
    if not ok:
        sys.exit(1)


def contar(model, *where):
    with SessionLocal() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


def esperar(cond, segundos=120):
    fim = time.time() + segundos
    while time.time() < fim:
        if cond():
            return True
        time.sleep(1)
    return False


# limpa execuções anteriores deste script
with SessionLocal() as s:
    for mb in s.scalars(select(MailboxAccount).where(MailboxAccount.nome == "Caixa de teste (aceite)")):
        s.execute(delete(Contact).where(Contact.id.in_(select(MailInteraction.contact_id).where(MailInteraction.mailbox_id == mb.id))))
        s.delete(mb)
    s.commit()

r = c.post("/login", data={"email": os.environ["ADMIN_EMAIL"], "senha": os.environ["ADMIN_PASSWORD"], "csrf_token": tok("/login")})
check(r.status_code == 303, "login do administrador")
check(c.get("/hunter").status_code == 200 and c.get("/hunter/caixas").status_code == 200, "páginas do Hunter abrem")

with tempfile.TemporaryDirectory() as d:
    gab = build_1000(Path(d) / "caixa.mbox")
    dados = (Path(d) / "caixa.mbox").read_bytes()
r = c.post("/hunter/caixas/mbox", data={"csrf_token": tok("/hunter/caixas"), "email": "eu@minhaempresa.com.br",
                                        "pasta": "misto", "nome": "Caixa de teste (aceite)"},
           files={"file": ("caixa.mbox", dados, "application/mbox")})
check(r.status_code == 303, f"enviar .mbox de 1.000 mensagens ({len(dados) // 1024} KB)")
with SessionLocal() as s:
    mb_id = s.scalar(select(MailboxAccount.id).where(MailboxAccount.nome == "Caixa de teste (aceite)"))
check(mb_id is not None, "caixa cadastrada")
check(esperar(lambda: contar(MailInteraction, MailInteraction.mailbox_id == mb_id) > 0), "worker varreu a caixa")
time.sleep(3)
n_contatos = len(gab["externos"])
with SessionLocal() as s:
    emails = list(s.scalars(select(Contact.email).join(MailInteraction, MailInteraction.contact_id == Contact.id)
                            .where(MailInteraction.mailbox_id == mb_id).distinct()))
    ids = list(s.scalars(select(Contact.id).where(Contact.email.in_(emails))))
check(len(emails) == len(set(emails)) == n_contatos, f"{n_contatos} contatos, sem duplicatas ({len(emails)})")
check(contar(ComplianceRecord, ComplianceRecord.entidade_tipo == "contact", ComplianceRecord.entidade_id.in_(ids)) >= n_contatos,
      "registro de conformidade para cada contato")
antes = (contar(Contact), contar(MailInteraction), contar(Evidence))

c.post(f"/hunter/caixas/{mb_id}/varrer", data={"csrf_token": tok("/hunter/caixas")})
time.sleep(6)
depois = (contar(Contact), contar(MailInteraction), contar(Evidence))
check(antes == depois, f"reprocessar não cria registros novos {antes} -> {depois}")
csv = c.get("/hunter/contatos.csv")
check(csv.status_code == 200 and len(csv.text.strip().splitlines()) >= n_contatos + 1, "exportação CSV")
print("Critério de aceite da Etapa 1: tudo certo.")
