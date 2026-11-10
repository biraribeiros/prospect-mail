"""Campanha manual: limpeza do HTML, email multipart, geração, aprovação, envio e telas. Mais o destino Trello e o mailer."""
import smtplib
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.core.models import Campaign, Company, Contact, CrmEvent, Deal, OutboundMessage, Suppression
from app.disparo import campaign as camp
from app.disparo import crm
from app.disparo.htmlmail import html_to_text, sanitize_html
from app.disparo.mime import build_email
from app.disparo.scheduler import schedule_approved, send_due
from tests.conftest import login, token
from tests.disparo.helpers import NOW, FakeSender, hunter_box, sender_box


# ---------- HTML ----------
def test_limpeza_tira_script_imagem_eventos_e_links_perigosos():
    sujo = ('<p onclick="x()">Oi <b>Maria</b><script>alert(1)</script></p><img src="http://t/x.png" onerror="y()">'
            '<a href="javascript:alert(1)">clique</a> <a href="https://ok.com.br/p?a=1&b=2" target="_blank" onclick="z()">site</a>'
            '<iframe src="http://evil"></iframe><style>p{display:none}</style><p style="text-align:center;color:red">meio</p>'
            '<p style="color:red">sem estilo</p><font>x</font><ul><li>um<li>dois</ul>')
    limpo = sanitize_html(sujo)
    for proibido in ("script", "alert", "<img", "onerror", "onclick", "javascript", "iframe", "display", "color", "target", "<font"):
        assert proibido not in limpo, proibido
    assert "<b>Maria</b>" in limpo and "clique" in limpo  # o texto do link perigoso fica, a marca some
    assert '<a href="https://ok.com.br/p?a=1&amp;b=2">site</a>' in limpo
    assert '<p style="text-align:center">meio</p>' in limpo and "<p>sem estilo</p>" in limpo
    assert limpo.count("<li>") == limpo.count("</li>") == 2 and limpo.count("<ul>") == limpo.count("</ul>") == 1


def test_html_para_texto_mantem_paragrafos_listas_e_links():
    t = html_to_text('<p>Olá, <b>Maria</b>.</p><p>Veja <a href="https://x.com.br/a">nosso site</a> ou <a href="mailto:a@x.com">a@x.com</a>.</p>'
                     "<ul><li>um</li><li>dois</li></ul><p>fim<br>linha</p>")
    assert t == "Olá, Maria.\n\nVeja nosso site (https://x.com.br/a) ou a@x.com.\n\n- um\n- dois\n\nfim\nlinha"


def test_email_com_html_e_multipart_com_descadastro_nos_dois():
    m = build_email(from_addr="c@envio.com.br", from_name=None, to_addr="a@b.com.br", subject="Oi", body="Texto", ws=1,
                    message_id="<x@envio.com.br>", html="<p>Olá <b>Maria</b></p>")
    assert m.get_content_type() == "multipart/alternative"
    partes = {p.get_content_type(): p.get_content() for p in m.iter_parts()}
    assert "Texto" in partes["text/plain"] and "/u/" in partes["text/plain"]
    assert "<b>Maria</b>" in partes["text/html"] and "/u/" in partes["text/html"] and "<!doctype html>" in partes["text/html"]
    assert m["List-Unsubscribe"].startswith("<http") and m["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    simples = build_email(from_addr="c@envio.com.br", from_name=None, to_addr="a@b.com.br", subject="Oi", body="Texto", ws=1,
                          message_id="<y@envio.com.br>")
    assert simples.get_content_type() == "text/plain"  # mensagens antigas continuam só texto


# ---------- geração e aprovação ----------
def nova(db, ws, assunto="Uma ideia para a {{empresa}}", html="<p>Olá, {{primeiro_nome}}.</p><p>Vi a <b>{{empresa}}</b> em {{cidade}}.</p>"):
    c = Campaign(workspace_id=ws.id, nome="Teste", assunto=assunto)
    camp.set_body(c, "html", html)
    db.add(c)
    db.flush()
    return c


def valido(db, ws, mail, nome="Ana", empresa="Alfa", origem="lista_texto", nota=50, **kw):
    co = Company(workspace_id=ws.id, razao_social=empresa.upper(), nome_fantasia=empresa, municipio="Patos de Minas", dominio=mail.split("@")[1])
    db.add(co)
    db.flush()
    ct = Contact(workspace_id=ws.id, company_id=co.id, email=mail, primeiro_nome=nome, nome_completo=nome, email_status="valido", origem=origem,
                 nota_final=nota, **kw)
    db.add(ct)
    db.flush()
    return ct


def test_gera_uma_mensagem_por_contato_valido_com_variaveis_preenchidas(db, world):
    ws = world.a
    c = nova(db, ws)
    valido(db, ws, "ana@alfa.com.br", "Ana", "Alfa")
    valido(db, ws, "bia@beta.com.br", "Bia", "Beta & Cia")
    arriscado = valido(db, ws, "caio@gama.com.br")
    arriscado.email_status = "arriscado"
    valido(db, ws, "dani@delta.com.br", origem="extensao_navegador")
    r = camp.generate(db, ws.id, c, origem="lista_texto", limite=10)
    db.commit()
    assert r["criadas"] == 2 and r["bloqueadas"] == 0 and c.status == "gerada"
    msgs = {db.get(Contact, m.contact_id).email: m for m in db.scalars(select(OutboundMessage))}
    ana = msgs["ana@alfa.com.br"]
    assert ana.status == "rascunho" and ana.campaign_id == c.id and ana.objetivo == "campanha" and ana.thread_raiz_id == ana.id
    assert ana.assunto == "Uma ideia para a Alfa" and "Olá, Ana." in ana.corpo and "Patos de Minas" in ana.corpo
    assert "<b>Alfa</b>" in ana.corpo_html and "{{" not in ana.corpo_html
    assert "Beta &amp; Cia" in msgs["bia@beta.com.br"].corpo_html  # dado do contato é escapado no HTML
    assert set(msgs) == {"ana@alfa.com.br", "bia@beta.com.br"}


def test_regras_do_publico_limite_dominio_supressao_e_ja_contatados(db, world):
    ws = world.a
    c = nova(db, ws)
    for i in range(4):
        valido(db, ws, f"p{i}@mesma.com.br", nota=90 - i)
    valido(db, ws, "sup@bloqueada.com.br")
    db.add(Suppression(workspace_id=ws.id, email_ou_dominio="bloqueada.com.br", motivo="descadastro", origem="manual"))
    antigo = valido(db, ws, "antigo@velha.com.br")
    db.add(OutboundMessage(workspace_id=ws.id, contact_id=antigo.id, assunto="a", corpo="b", status="enviada", passo=1,
                           enviada_em=NOW - timedelta(days=10)))
    db.flush()
    db.execute(OutboundMessage.__table__.update().values(criado_em=NOW - timedelta(days=10)))  # contatado há 10 dias
    for i in range(3):
        valido(db, ws, f"x{i}@outra{i}.com.br", nota=10)
    r = camp.generate(db, ws.id, c, limite=5, max_dominio=2, agora=NOW)
    assert r["suprimidos"] == 1 and r["ja_contatados"] == 1 and r["excesso_dominio"] == 2
    assert r["criadas"] == 5 and r["limite"] is False
    assert sorted(db.get(Contact, m.contact_id).email for m in db.scalars(select(OutboundMessage).where(OutboundMessage.campaign_id == c.id)))[:2] \
        == ["p0@mesma.com.br", "p1@mesma.com.br"]  # a maior nota do domínio entra primeiro
    r2 = camp.generate(db, ws.id, c, limite=2, max_dominio=2, agora=NOW)  # gerar de novo refaz só os rascunhos
    assert r2["criadas"] == 2 and r2["limite"] is True
    assert db.query(OutboundMessage).filter_by(campaign_id=c.id, status="rascunho").count() == 2


def test_contato_sem_dado_da_variavel_fica_bloqueado_com_motivo(db, world):
    ws = world.a
    c = nova(db, ws)
    valido(db, ws, "sem@alfa.com.br", nome="")
    camp.generate(db, ws.id, c)
    m = db.scalar(select(OutboundMessage))
    assert m.status == "bloqueada" and "primeiro_nome" in m.motivo


def test_conferencia_de_regras_e_avisos(db, world):
    ok = nova(db, world.a)
    assert camp.check_campaign(ok) == ([], [])
    ruim = nova(db, world.a, assunto="", html="<p>Oi {{sobrenome}} " + "palavra " * 310 + "</p>")
    erros, _ = camp.check_campaign(ruim)
    assert any("assunto" in e.lower() for e in erros) and any("{{sobrenome}}" in e for e in erros) and any("longa demais" in e for e in erros)
    links = nova(db, world.a, html="<p>" + " ".join(f"https://x.com.br/{i}" for i in range(4)) + "</p>")
    assert any("Links demais" in e for e in camp.check_campaign(links)[0])
    _, avisos = camp.check_campaign(nova(db, world.a, html="<p>Isso é GRÁTIS e URGENTE para VOCÊ AGORA MESMO</p>"))
    assert any("spam" in a.lower() for a in avisos) and any("MAIÚSCULAS" in a for a in avisos)


def test_aprovar_confere_a_nota_de_spam_e_so_depois_libera(db, world):
    ws = world.a
    c = nova(db, ws)
    valido(db, ws, "ana@alfa.com.br")
    camp.generate(db, ws.id, c)
    alto = camp.approve(db, ws.id, c, checker=lambda raw: 8.0)
    assert "spam" in alto["erros"][0].lower() and db.scalar(select(OutboundMessage)).status == "rascunho"
    ok = camp.approve(db, ws.id, c, checker=lambda raw: 1.2)
    assert ok["aprovadas"] == 1 and db.scalar(select(OutboundMessage)).status == "aprovada" and float(db.scalar(select(OutboundMessage)).spam_score) == 1.2
    assert "erros" in camp.approve(db, ws.id, c, checker=lambda raw: 1.0)  # nada em rascunho


def test_do_rascunho_ao_envio_multipart_pelo_agendador(db, world):
    ws = world.a
    hunter_box(db, ws)
    sender_box(db, ws)
    c = nova(db, ws)
    valido(db, ws, "ana@alfa.com.br", "Ana", "Alfa")
    camp.generate(db, ws.id, c)
    camp.approve(db, ws.id, c, checker=lambda raw: 1.0)
    db.commit()
    schedule_approved(db, ws.id, NOW)
    sender = FakeSender()
    out = send_due(db, ws.id, sender, NOW + timedelta(hours=4))
    assert out["enviadas"] == 1
    msg = sender.enviadas[0][1]
    assert msg.get_content_type() == "multipart/alternative" and msg["Subject"] == "Uma ideia para a Alfa"
    partes = {p.get_content_type(): p.get_content() for p in msg.iter_parts()}
    assert "Olá, Ana." in partes["text/plain"] and "<b>Alfa</b>" in partes["text/html"]


# ---------- telas ----------
def corpo_form(**extra):
    return {"nome": "Via novembro", "assunto": "Uma ideia para a {{empresa}}", "formato": "html",
            "corpo_html": '<p>Olá, {{primeiro_nome}}.</p><script>alert(1)</script><p>Fale com a <b>{{empresa}}</b>.</p>', **extra}


def test_telas_da_campanha_criar_gerar_aprovar(logged, db, world, monkeypatch):
    from app.web.routes import campanhas

    async def sem_fila(*a, **k):
        return None
    monkeypatch.setattr(campanhas, "enqueue", sem_fila)
    monkeypatch.setattr(camp, "spam_check", lambda *a, **k: (1.0, "rspamd"))
    assert logged.get("/disparo/campanhas").status_code == 200
    editor = logged.get("/disparo/campanhas/nova").text
    assert 'id="tab-visual"' in editor and 'id="tab-codigo"' in editor and "Inserir variável" in editor
    tok = token(logged, "/disparo/campanhas/nova")
    r = logged.post("/disparo/campanhas", data={**corpo_form(), "csrf_token": tok})
    assert r.status_code == 303
    c = db.scalar(select(Campaign))
    assert c.workspace_id == world.a.id and "<script" not in c.corpo_html and "alert" not in c.corpo_html and "{{empresa}}" in c.corpo_texto
    valido(db, world.a, "ana@alfa.com.br", "Ana", "Alfa")
    db.commit()
    det = logged.get(f"/disparo/campanhas/{c.id}").text
    assert "Prévia" in det and "Uma ideia para a Empresa Exemplo" in det and "Todos os válidos (1)" in det
    tok = token(logged, f"/disparo/campanhas/{c.id}")
    assert logged.post(f"/disparo/campanhas/{c.id}/gerar", data={"csrf_token": tok, "limite": "10", "max_dominio": "2"}).status_code == 303
    assert "Aprovar 1 mensagem(ns) e agendar" in logged.get(f"/disparo/campanhas/{c.id}").text
    assert logged.post(f"/disparo/campanhas/{c.id}/aprovar", data={"csrf_token": tok}).status_code == 303
    db.expire_all()
    assert db.scalar(select(OutboundMessage)).status == "aprovada"
    assert logged.post(f"/disparo/campanhas/{c.id}/conferir", data={"csrf_token": tok}).status_code == 303


def test_so_admin_ou_aprovador_aprova_e_workspaces_sao_isolados(client, db, world):
    c = nova(db, world.a)
    db.commit()
    login(client, world.operador_a.email)
    tok = token(client, "/disparo/campanhas")
    assert client.post(f"/disparo/campanhas/{c.id}/aprovar", data={"csrf_token": tok}).status_code == 403
    assert client.post("/disparo/campanhas", data={**corpo_form(), "csrf_token": tok}).status_code == 303  # operador escreve
    outro = type(client)(client.app, follow_redirects=False)
    login(outro, world.user_b.email)
    assert outro.get(f"/disparo/campanhas/{c.id}").status_code == 404


# ---------- Trello por email ----------
def deal(db, ws):
    co = Company(workspace_id=ws.id, razao_social="ALFA LTDA", nome_fantasia="Alfa #1", cnpj="11222333000181", uf="SP", municipio="Campinas")
    db.add(co)
    db.flush()
    ct = Contact(workspace_id=ws.id, company_id=co.id, email="ana@alfa.com", nome_completo="Ana @Lima", cargo="Diretora", telefone="1999")
    db.add(ct)
    db.flush()
    d = Deal(workspace_id=ws.id, company_id=co.id, contact_id=ct.id, estagio="interessado")
    db.add(d)
    db.commit()
    return d


def test_oportunidade_vira_cartao_no_trello_por_email(db, world, monkeypatch):
    enviados = []
    monkeypatch.setattr(crm, "get_settings", lambda: Settings(trello_board_email="bira+q1w2@boards.trello.com", crm_webhook_url="http://x.test/h",
                                                              public_base_url="http://localhost:8000"))
    monkeypatch.setattr(crm, "send_mail", lambda to, assunto, corpo, from_addr=None: enviados.append((to, assunto, corpo)))
    d = deal(db, world.a)
    ev = crm.send_event(db, world.a.id, d, "interessado", "Quero conversar!")
    assert ev.status == "enviado" and ev.tentativas == 1
    (to, assunto, corpo), = enviados
    assert to == ["bira+q1w2@boards.trello.com"] and "@" not in assunto and "#" not in assunto  # sem menção nem etiqueta acidental
    assert assunto.startswith("Interessado: Alfa 1 - Ana Lima")
    assert "Quero conversar!" in corpo and "ana@alfa.com" in corpo and "Campinas/SP" in corpo and "/disparo/respostas" in corpo
    crm.send_event(db, world.a.id, d, "interessado", "")
    assert len(enviados) == 1  # idempotente: o mesmo evento não cria dois cartões


def test_trello_fora_do_ar_fica_falhou_e_pode_reenviar(db, world, monkeypatch):
    monkeypatch.setattr(crm, "get_settings", lambda: Settings(trello_board_email="q@boards.trello.com"))

    def falha(*a, **k):
        raise ConnectionRefusedError()
    monkeypatch.setattr(crm, "send_mail", falha)
    d = deal(db, world.a)
    assert crm.send_event(db, world.a.id, d, "interessado", "").status == "falhou"
    enviados = []
    monkeypatch.setattr(crm, "send_mail", lambda *a, **k: enviados.append(a))
    assert crm.resend_pending(db, world.a.id) == 1 and len(enviados) == 1
    assert db.scalar(select(CrmEvent)).status == "enviado"


# ---------- email interno ----------
def test_mailer_usa_starttls_e_login_quando_configurado(monkeypatch):
    from app.core import mailer

    passos = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None): passos.append(("conecta", host, port))
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, context=None): passos.append(("starttls",))
        def login(self, u, p): passos.append(("login", u, p))
        def send_message(self, msg): passos.append(("envia", msg["To"], msg["From"], msg["Subject"]))
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mailer, "get_settings", lambda: Settings(smtp_host="smtp.x.com", smtp_port=587, smtp_security="starttls", smtp_user="u",
                                                                  smtp_password="p", smtp_from="avisos@x.com"))
    mailer.send_mail(["a@b.com", "c@d.com"], "Oi", "corpo")
    assert passos == [("conecta", "smtp.x.com", 587), ("starttls",), ("login", "u", "p"), ("envia", "a@b.com, c@d.com", "avisos@x.com", "Oi")]
