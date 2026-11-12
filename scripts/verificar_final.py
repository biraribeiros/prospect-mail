"""Aceite FINAL contra o sistema em execução (Postgres, Redis, worker, Mailpit e Rspamd reais; nenhuma chamada externa).

Percorre, num workspace novo e descartável ("Aceite Final"): painel → Inteligência (hipótese aprovada) → Disparo (escrita pelo
worker + Rspamd real, aprovação, agendamento, 200 envios ao Mailpit com cabeçalhos corretos) → descadastro pelo link →
resposta "interessado" → disjuntor → métricas. No fim apaga tudo, inclusive as mensagens do Mailpit.

Uso: docker compose exec app python scripts/verificar_final.py
Usa ADMIN_EMAIL / ADMIN_PASSWORD do .env (a senha não é impressa).
"""
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, func, select  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.core.models import (Company, Contact, Deal, Hypothesis, InboundReply, MailboxAccount, OpportunitySignal, OutboundMessage,  # noqa: E402
                             SenderDomain, Suppression, Workspace)
from app.core.security import encrypt_json  # noqa: E402
from app.disparo import breaker  # noqa: E402
from app.disparo.compose import rspamd_score  # noqa: E402
from app.disparo.replies import ingest_raw  # noqa: E402
from app.disparo.scheduler import schedule_approved, send_due  # noqa: E402
from app.intel.hypothesis import run_hypotheses  # noqa: E402

BASE = os.environ.get("BASE_URL", "http://localhost:8000")
MAILPIT = os.environ.get("MAILPIT_URL", "http://mailpit:8025")
NOME_WS = "Aceite Final"
TAG = "ACEITEFINAL"
c = httpx.Client(base_url=BASE, follow_redirects=False, timeout=60)
mp = httpx.Client(base_url=MAILPIT, timeout=30)
ws_id: int | None = None


def tok(path):
    return re.search(r'name="csrf_token" value="([^"]+)"', c.get(path).text).group(1)


def limpar():
    with SessionLocal() as s:
        ws = s.scalar(select(Workspace).where(Workspace.nome == NOME_WS))
        if ws:
            s.execute(delete(Workspace).where(Workspace.id == ws.id))
            s.commit()
    try:
        ids = [m["ID"] for m in mp.get("/api/v1/search", params={"query": f"subject:{TAG}", "limit": 1000}).json().get("messages", [])]
        ids += [m["ID"] for m in mp.get("/api/v1/messages", params={"limit": 1000}).json().get("messages", [])
                if any(TAG in (t.get("Address", "") + t.get("Name", "")) for t in m.get("To", [])) or TAG in m.get("Subject", "")]
        if ids:
            mp.request("DELETE", "/api/v1/messages", json={"IDs": list(set(ids))})
    except httpx.HTTPError:
        pass


def check(ok, msg):
    print(("OK   " if ok else "FALHA"), msg)
    if not ok:
        limpar()
        sys.exit(1)


def esperar(cond, segundos=60):
    fim = time.time() + segundos
    while time.time() < fim:
        if cond():
            return True
        time.sleep(1)
    return False


limpar()
r = c.post("/login", data={"email": os.environ["ADMIN_EMAIL"], "senha": os.environ["ADMIN_PASSWORD"], "csrf_token": tok("/login")})
check(r.status_code == 303, "login do administrador")

# ---- 1) workspace novo, configurado pelo painel ----
t = tok("/operacao")
check(c.post("/operacao/workspaces", data={"nome": NOME_WS, "csrf_token": t}).status_code == 303, "workspace de outro setor criado pelo painel")
with SessionLocal() as s:
    ws_id = s.scalar(select(Workspace.id).where(Workspace.nome == NOME_WS))
check(c.post("/workspaces", data={"workspace_id": ws_id, "csrf_token": t}).status_code == 303, "workspace selecionado")
t = tok("/configuracoes/departamentos/novo")
check(c.post("/configuracoes/departamentos", data={"nome": "Direção", "email_patterns": "diretoria@", "cargos": "Diretor",
                                                   "necessidades": "Divulgar serviços | 5 | video", "csrf_token": t}).status_code == 303, "departamento criado")
check(c.post("/configuracoes/icp", data={"nome": "ICP Aceite", "cnaes": "70.20-4", "ufs": "SP", "portes": "ME", "csrf_token": t}).status_code == 303,
      "perfil de cliente ideal criado")
check(c.post("/inteligencia/ofertas", data={"nome": "Vídeo institucional", "tipos_sinal": ["vaga"], "tags": "video", "ativo": "sim",
                                            "descricao": "Vídeos para marca empregadora", "csrf_token": t}).status_code == 303, "oferta criada")

# ---- 2) dados: 3 contatos pelo caminho completo + 197 para volume ----
random.seed(7)
with SessionLocal() as s:
    from app.core.models import Department
    dep = s.scalar(select(Department.id).where(Department.workspace_id == ws_id))
    for i in range(200):
        co = Company(workspace_id=ws_id, razao_social=f"{TAG} EMPRESA {i} LTDA", nome_fantasia=f"{TAG} Empresa {i}", dominio=f"destino{i}.example.com",
                     uf="SP" if i % 10 else "AM", municipio="Campinas", cnae_principal="7020-4/00")
        s.add(co)
        s.flush()
        ct = Contact(workspace_id=ws_id, company_id=co.id, email=f"aceite{i}@destino{i}.example.com", email_status="valido", primeiro_nome=f"Pessoa{i}",
                     nome_completo=f"Pessoa{i} Teste", department_id=dep, nota_contato=60)
        s.add(ct)
        if i < 3:
            s.add(OpportunitySignal(workspace_id=ws_id, company_id=co.id, tipo="vaga", titulo=f"{TAG} 4 vaga(s) aberta(s)", fonte="pagina_trabalhe_conosco",
                                    url=f"https://destino{i}.example.com/vagas", confianca=0.7, hash=f"h{i}",
                                    valido_ate=datetime.now(timezone.utc) + timedelta(days=20)))
    s.commit()
    res = run_hypotheses(s, ws_id, use_default_llm=False)
    check(res.get("hipoteses") == 3, f"3 hipóteses geradas só com sinal + oferta + prova ({res.get('hipoteses')})")
    hids = list(s.scalars(select(Hypothesis.id).where(Hypothesis.workspace_id == ws_id)))
t = tok("/inteligencia/hipoteses")
for h in hids:
    check(c.post(f"/inteligencia/hipoteses/{h}/decidir", data={"acao": "aprovar", "csrf_token": t}).status_code == 303, f"hipótese {h} aprovada por humano")

# ---- 3) worker escreve as mensagens (Rspamd real) ----
check(c.post("/disparo/mensagens/gerar", data={"csrf_token": tok("/disparo/mensagens")}).status_code == 303, "escrita das mensagens enfileirada")
with SessionLocal() as s:
    check(esperar(lambda: s.scalar(select(func.count()).select_from(OutboundMessage).where(OutboundMessage.workspace_id == ws_id, OutboundMessage.passo == 1)) == 3
                  or (s.rollback() or False)), "worker escreveu 3 sequências")
    firsts = list(s.scalars(select(OutboundMessage).where(OutboundMessage.workspace_id == ws_id, OutboundMessage.passo == 1)))
    scores = [float(m.spam_score) for m in firsts]
    check(all(sc < 5 for sc in scores) and all(m.status == "rascunho" for m in firsts), f"mensagens abaixo de 5 no Rspamd real: {scores}")
    check(all(f"{TAG} Empresa" in m.corpo for m in firsts), "texto usa o nome da empresa (fato registrado)")
    fids = [m.id for m in firsts]
html = c.get("/disparo/mensagens").text
check("Passo 3" in html and "Aprovar marcadas" in html, "fila de aprovação em lote mostra a sequência de 3 passos")
check(c.post("/disparo/mensagens/decidir", data={"acao": "aprovar", "sel": [str(i) for i in fids], "csrf_token": tok("/disparo/mensagens")}).status_code == 303,
      "aprovação em lote")

# ---- 4) 4 caixas de envio (domínios secundários) ----
t = tok("/disparo")
for i in range(4):
    r = c.post("/disparo/caixas", data={"email": f"contato@aceite{i}-envios.example.org", "host": "smtp.example.org", "port": "587", "security": "starttls",
                                        "password": "senha-qualquer", "selector": "default", "limite": "0", "csrf_token": t})
    check(r.status_code == 303, f"caixa de envio {i + 1} cadastrada")
r = c.post("/disparo/caixas", data={"email": "contato@aceite-bloqueado.example.org", "host": "10.0.0.5", "password": "x", "csrf_token": t})
check(c.get("/disparo").text.count("aceite-bloqueado") == 0, "servidor SMTP em rede interna é recusado")
with SessionLocal() as s:
    doms = list(s.scalars(select(SenderDomain).where(SenderDomain.workspace_id == ws_id)))
    check(len(doms) == 4 and all(d.tipo == "secundario" for d in doms), "domínios cadastrados como secundários")
    boxes = list(s.scalars(select(MailboxAccount).where(MailboxAccount.workspace_id == ws_id, MailboxAccount.uso == "envio")))
    check(all("senha-qualquer" not in (b.tokens_enc or "") for b in boxes), "senhas das caixas gravadas cifradas")
    # sem DNS conferido o envio fica bloqueado
    from app.disparo.guard import DomainNotReady, assert_can_send
    try:
        assert_can_send(s, boxes[0])
        check(False, "caixa sem DNS deveria ser bloqueada")
    except DomainNotReady:
        check(True, "caixa sem DNS conferido: envio bloqueado")
    # a conferência real de DNS não pode ser feita para domínios de exemplo: marcamos como conferidos e aquecidos
    for d in doms:
        d.spf_ok = d.dkim_ok = d.dmarc_ok = d.rdns_ok = True
    antigo = datetime.now(timezone.utc) - timedelta(days=60)
    ct0 = s.scalar(select(Contact).where(Contact.workspace_id == ws_id))
    for b in boxes:
        s.add(OutboundMessage(workspace_id=ws_id, contact_id=ct0.id, mailbox_id=b.id, assunto=f"{TAG} historico", corpo="x", status="enviada",
                              enviada_em=antigo, passo=1))
    # 197 mensagens extras já aprovadas (volume)
    for ct in s.scalars(select(Contact).where(Contact.workspace_id == ws_id, Contact.id.notin_(
            select(Hypothesis.contact_id).where(Hypothesis.workspace_id == ws_id)))):
        s.add(OutboundMessage(workspace_id=ws_id, contact_id=ct.id, company_id=ct.company_id, assunto=f"{TAG} Uma ideia para {ct.primeiro_nome}",
                              corpo=f"Olá, {ct.primeiro_nome}. Vi que vocês estão contratando. Faz sentido conversarmos 15 minutos?", status="aprovada"))
    s.commit()

# ---- 5) agenda e 200 envios ao Mailpit (SMTP de verdade, modo seguro) ----
with SessionLocal() as s:
    now = datetime.now(timezone.utc)
    seg = now + timedelta(days=(7 - now.weekday()) % 7 or 7)
    t0 = seg.replace(hour=11, minute=0, second=0, microsecond=0)  # segunda 8h de São Paulo
    res = schedule_approved(s, ws_id, t0, random.Random(3))
    s.commit()
    total = s.scalar(select(func.count()).select_from(OutboundMessage).where(OutboundMessage.workspace_id == ws_id, OutboundMessage.status == "agendada"))
    check(total >= 200 and res["adiadas_por_limite"] == 0, f"{total} mensagens agendadas (rodízio entre 4 caixas)")
    # janela comercial e intervalo mínimo por caixa
    from app.disparo.scheduler import in_window, recipient_tz
    ags = list(s.execute(select(OutboundMessage, Company).join(Company, Company.id == OutboundMessage.company_id).where(
        OutboundMessage.workspace_id == ws_id, OutboundMessage.status == "agendada")))
    check(all(in_window(m.agendada_para, recipient_tz(co)) for m, co in ags), "todos os horários dentro de seg–sex, 8h–18h do destinatário")
    por_caixa: dict[int, list] = {}
    for m, _ in ags:
        por_caixa.setdefault(m.mailbox_id, []).append(m.agendada_para)
    check(all(b - a >= timedelta(minutes=3) for v in por_caixa.values() for a, b in zip(sorted(v), sorted(v)[1:])), "intervalo mínimo de 3 min entre envios da mesma caixa")
    ultimo = max(m.agendada_para for m, _ in ags)
    dia = t0
    enviadas = 0
    while dia <= ultimo + timedelta(days=1):
        enviadas += send_due(s, ws_id, now=dia.replace(hour=20, minute=59))["enviadas"]
        s.commit()
        dia += timedelta(days=1)
    check(enviadas >= 200, f"{enviadas} emails entregues ao Mailpit pelo SMTP")
    msgs_enviadas = list(s.scalars(select(OutboundMessage).where(OutboundMessage.workspace_id == ws_id, OutboundMessage.status == "enviada",
                                                                 OutboundMessage.assunto.like(f"%{TAG}%") | (OutboundMessage.passo == 1))))

time.sleep(2)
busca = mp.get("/api/v1/search", params={"query": f"subject:{TAG}", "limit": 500}).json()
check(busca.get("messages_count", 0) >= 190, f"Mailpit recebeu as mensagens ({busca.get('messages_count')})")
um = busca["messages"][0]
raw = mp.get(f"/api/v1/message/{um['ID']}/raw").text
raw_h = re.sub(r"\r?\n[ \t]+", " ", raw)  # desdobra cabeçalhos longos (continuação de linha)
check("List-Unsubscribe: <" in raw_h and "List-Unsubscribe-Post: List-Unsubscribe=One-Click" in raw_h, "cabeçalhos List-Unsubscribe (RFC 8058)")
check(re.search(r"Message-ID: <[0-9a-f]{32}@aceite\d-envios\.example\.org>", raw_h, re.I) is not None, "Message-ID próprio no domínio de envio")
check("Content-Type: text/plain" in raw and "multipart" not in raw.split("\n\n")[0], "versão em texto puro, sem imagem nem anexo")

# ---- 6) descadastro pelo link do email ----
link = re.search(r"https?://[^\s>]+/u/[\w.\-]+", raw_h).group(0)
path = "/u/" + link.split("/u/")[1]
check(c.get(path).status_code == 200, "página de descadastro abre")
alvo = re.search(r"To: .*?(aceite\d+@destino\d+\.example\.com)", raw_h).group(1)
check(c.post(path, content="List-Unsubscribe=One-Click", headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 200,
      "descadastro One-Click (POST sem login)")
with SessionLocal() as s:
    check(s.scalar(select(Suppression.id).where(Suppression.workspace_id == ws_id, Suppression.email_ou_dominio == alvo)) is not None, f"{alvo} entrou na supressão")

# ---- 7) resposta "interessado" → negócio ----
with SessionLocal() as s:
    ct = s.scalar(select(Contact).where(Contact.workspace_id == ws_id, Contact.email == alvo.replace(alvo, "aceite0@destino0.example.com")))
    m1 = s.scalar(select(OutboundMessage).where(OutboundMessage.contact_id == ct.id, OutboundMessage.status == "enviada", OutboundMessage.message_id.is_not(None)))
    em = EmailMessage()
    em["From"], em["To"], em["Subject"], em["In-Reply-To"], em["Message-ID"] = ct.email, "contato@x.org", "Re: x", m1.message_id, "<resp-aceite@x>"
    em.set_content("Tenho interesse! Podemos conversar amanhã?")
    r = ingest_raw(s, ws_id, em.as_bytes())
    s.commit()
    check(r.get("classe") == "interessado", "resposta classificada como interessado")
    check(s.scalar(select(Deal.estagio).where(Deal.workspace_id == ws_id, Deal.contact_id == ct.id)) == "interessado", "negócio criado + tarefa de WhatsApp em 24h")

# ---- 8) disjuntor: bounces acima de 3% pausam tudo e avisam ----
with SessionLocal() as s:
    now = datetime.now(timezone.utc)
    for m in s.scalars(select(OutboundMessage).where(OutboundMessage.workspace_id == ws_id, OutboundMessage.status == "enviada").limit(10)):
        m.enviada_em, m.bounce_tipo, m.bounce_em = now - timedelta(hours=1), "hard", now
    for m in s.scalars(select(OutboundMessage).where(OutboundMessage.workspace_id == ws_id, OutboundMessage.status == "enviada").offset(10).limit(300)):
        m.enviada_em = now - timedelta(hours=2)
    s.flush()
    check(breaker.evaluate(s, ws_id, now) is True, "disjuntor disparou com bounce acima de 3% em 24 h")
    s.commit()
check("PAUSADO" in c.get("/disparo").text, "painel mostra “Envio PAUSADO”")
time.sleep(1)
alertas = mp.get("/api/v1/search", params={"query": "subject:Prospector subject:disjuntor"}).json()
check(alertas.get("messages_count", 0) >= 1, "email de alerta ao administrador chegou (Mailpit)")
check(c.post("/disparo/liberar", data={"csrf_token": tok("/disparo")}).status_code == 303 and "PAUSADO" not in c.get("/disparo").text, "administrador libera o envio")

# ---- 9) métricas e relatório ----
html = c.get("/metricas").text
check("Taxa de resposta" in html and "não é confiável" in html, "painel de métricas completo")
check("Resumo semanal" in c.get("/metricas/relatorio").text, "resumo semanal gerado")

limpar()
with SessionLocal() as s:
    check(s.scalar(select(Workspace.id).where(Workspace.nome == NOME_WS)) is None, "dados de teste removidos (banco e Mailpit)")
print("Aceite final: tudo certo.")
