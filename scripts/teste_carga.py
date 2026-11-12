"""Teste de carga (F16): mede onde está o gargalo — agenda, painel ou SMTP — num workspace descartável.

Uso: docker compose exec app python scripts/teste_carga.py [N_CONTATOS=3000]
Não envia nada para fora: o SMTP vai para o Mailpit. Apaga tudo no final.
"""
import os
import random
import re
import smtplib
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, select  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.core.models import Company, Contact, MailboxAccount, OutboundMessage, SenderDomain, Workspace  # noqa: E402
from app.core.security import encrypt_json  # noqa: E402
from app.disparo.mime import build_email, new_message_id  # noqa: E402
from app.disparo.scheduler import schedule_approved  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
NOME = "Carga Descartavel"
BASE = os.environ.get("BASE_URL", "http://localhost:8000")


def tempo(f):
    t = time.perf_counter()
    r = f()
    return r, time.perf_counter() - t


with SessionLocal() as s:
    s.execute(delete(Workspace).where(Workspace.nome == NOME))
    ws = Workspace(nome=NOME)
    s.add(ws)
    s.flush()
    ws_id = ws.id
    antigo = datetime.now(timezone.utc) - timedelta(days=60)
    boxes = []
    for i in range(10):
        d = SenderDomain(workspace_id=ws_id, nome=f"carga{i}.example.org", spf_ok=True, dkim_ok=True, dmarc_ok=True, rdns_ok=True)
        s.add(d)
        s.flush()
        b = MailboxAccount(workspace_id=ws_id, tipo="smtp", nome=f"c{i}", uso="envio", sender_domain_id=d.id, dominio=d.nome,
                           tokens_enc=encrypt_json({"from": f"contato@{d.nome}"}))
        s.add(b)
        boxes.append(b)
    s.flush()
    cos = [Company(workspace_id=ws_id, razao_social=f"CARGA {i}", uf="SP") for i in range(N)]
    s.add_all(cos)
    s.flush()
    cts = [Contact(workspace_id=ws_id, company_id=co.id, email=f"carga{i}@dest{i}.example.com", email_status="valido", primeiro_nome="X")
           for i, co in enumerate(cos)]
    s.add_all(cts)
    s.flush()
    for b in boxes:
        s.add(OutboundMessage(workspace_id=ws_id, contact_id=cts[0].id, mailbox_id=b.id, assunto="h", corpo="h", status="enviada", enviada_em=antigo))
    s.add_all([OutboundMessage(workspace_id=ws_id, contact_id=ct.id, company_id=ct.company_id, assunto="Uma ideia", corpo="Olá. Faz sentido?",
                               status="aprovada") for ct in cts])
    s.commit()
    print(f"{N} contatos, {N} mensagens aprovadas, 10 caixas aquecidas")

    # 1) agendamento de todas as mensagens
    seg = datetime.now(timezone.utc) + timedelta(days=(7 - datetime.now(timezone.utc).weekday()) % 7 or 7)
    res, dt = tempo(lambda: schedule_approved(s, ws_id, seg.replace(hour=11, minute=0, second=0, microsecond=0), random.Random(1)))
    s.commit()
    dias = len({m.agendada_para.date() for m in s.scalars(select(OutboundMessage).where(OutboundMessage.workspace_id == ws_id, OutboundMessage.status == "agendada"))})
    print(f"agendamento: {res['agendadas']} mensagens em {dt:.1f}s ({res['agendadas'] / dt:.0f}/s) — espalhadas por {dias} dias úteis (limite: 10 caixas x 50/dia)")

# 2) SMTP: custo por mensagem (Mailpit local, sem rede externa)
amostras = []
with smtplib.SMTP("mailpit", 1025) as smtp:
    for i in range(100):
        m = build_email(from_addr="contato@carga0.example.org", from_name=None, to_addr=f"carga{i}@dest{i}.example.com", subject="CARGA", body="x", ws=ws_id,
                        message_id=new_message_id("carga0.example.org"))
        _, d = tempo(lambda: smtp.send_message(m))
        amostras.append(d)
print(f"SMTP local: {statistics.mean(amostras) * 1000:.1f} ms por mensagem (o intervalo de segurança entre envios é de 3 a 15 MINUTOS por caixa)")

# 3) painel sob carga: 20 acessos simultâneos
if os.environ.get("ADMIN_EMAIL"):
    c = httpx.Client(base_url=BASE, timeout=60, follow_redirects=False)
    tk = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").text).group(1)
    c.post("/login", data={"email": os.environ["ADMIN_EMAIL"], "senha": os.environ["ADMIN_PASSWORD"], "csrf_token": tk})
    c.post("/workspaces", data={"workspace_id": ws_id, "csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', c.get("/workspaces").text).group(1)})
    lat = []

    def req(_):
        t = time.perf_counter()
        r = c.get("/hunter/empresas")
        lat.append(time.perf_counter() - t)
        return r.status_code
    with ThreadPoolExecutor(20) as ex:
        codes = list(ex.map(req, range(60)))
    lat.sort()
    print(f"painel (lista de {N} empresas), 60 acessos / 20 simultâneos: p50 {lat[len(lat) // 2] * 1000:.0f} ms, p95 {lat[int(len(lat) * .95)] * 1000:.0f} ms, erros: {sum(x != 200 for x in codes)}")

with SessionLocal() as s:
    s.execute(delete(Workspace).where(Workspace.nome == NOME))
    s.commit()
try:
    mp = httpx.Client(base_url="http://mailpit:8025", timeout=30)
    ids = [m["ID"] for m in mp.get("/api/v1/search", params={"query": "subject:CARGA", "limit": 500}).json().get("messages", [])]
    if ids:
        mp.request("DELETE", "/api/v1/messages", json={"IDs": ids})
except httpx.HTTPError:
    pass
print("dados de teste removidos")
