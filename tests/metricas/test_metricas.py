"""Etapa 9: 4 visões, teste entre ICPs (veredito), A/B, relatório semanal. Tudo com dados simulados."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.models import (Company, Contact, Deal, Hypothesis, IcpProfile, InboundReply, JobRun, Offer, OpportunitySignal,
                             OutboundMessage)
from app.metricas import queries as q
from app.metricas.stats import compare_groups, fisher_two_sided
from tests.conftest import login
from tests.disparo.helpers import NOW

ENVIO = NOW - timedelta(days=40)  # primeiros contatos enviados há 40 dias: janela de 21 dias já fechou


def icp(db, ws, nome, uf):
    i = IcpProfile(workspace_id=ws.id, nome=nome, ufs=[uf])
    db.add(i)
    db.commit()
    return i


def coorte(db, ws, icp_obj, n, reunioes, *, prefix, variante=None, respostas=0, dias_ate_reuniao=5, envio=ENVIO, uf="SP"):
    """n contatos abordados neste ICP; `reunioes` deles marcaram reunião; `respostas` responderam (inclui os das reuniões)."""
    for i in range(n):
        co = Company(workspace_id=ws.id, razao_social=f"{prefix} {i}", uf=uf)
        db.add(co)
        db.flush()
        ct = Contact(workspace_id=ws.id, company_id=co.id, email=f"{prefix}{i}@x{prefix}.com", email_status="valido")
        db.add(ct)
        db.flush()
        m = OutboundMessage(workspace_id=ws.id, contact_id=ct.id, company_id=co.id, icp_id=icp_obj.id if icp_obj else None, passo=1,
                            assunto="a", corpo="b", status="enviada", enviada_em=envio, message_id=f"<{prefix}{i}@v>", variante=variante)
        db.add(m)
        db.flush()
        if i < reunioes:
            db.add(Deal(workspace_id=ws.id, company_id=co.id, contact_id=ct.id, estagio="reuniao",
                        reuniao_em=envio + timedelta(days=dias_ate_reuniao)))
        if i < max(respostas, reunioes):
            db.add(InboundReply(workspace_id=ws.id, outbound_id=m.id, contact_id=ct.id, message_key=f"{prefix}{i}", classe="interessado"))
    db.commit()


# ---------- estatística ----------
def test_fisher_exato():
    assert fisher_two_sided(10, 40, 10, 40) == pytest.approx(1.0)
    assert fisher_two_sided(20, 40, 5, 40) < 0.001
    assert 0.4 < fisher_two_sided(8, 40, 5, 40) < 0.7
    assert fisher_two_sided(0, 40, 0, 40) == 1.0


def test_veredito_regras():
    assert compare_groups("A", 39, 5, "B", 60, 5).status == "insuficiente"
    assert compare_groups("A", 50, 20, "B", 50, 5, maduros=False).status == "aguardando"
    v = compare_groups("A", 50, 20, "B", 50, 5)
    assert v.status == "vencedor" and v.vencedor == "A" and v.p_valor < 0.05
    assert compare_groups("A", 50, 6, "B", 50, 7).status == "empate"
    assert compare_groups("A", 50, 5, "B", 50, 20).vencedor == "B"


# ---------- teste entre ICPs ----------
def test_icp_vencedor_correto_no_modo_teste(db, world):
    ws = world.a
    a, b = icp(db, ws, "Consultorias SP", "SP"), icp(db, ws, "Clínicas RJ", "RJ")
    coorte(db, ws, a, 60, 18, prefix="a")
    coorte(db, ws, b, 60, 4, prefix="b")
    r = q.icp_test(db, ws.id, a.id, b.id, agora=NOW)
    assert r["a"]["abordados"] == 60 and r["a"]["reunioes"] == 18 and r["b"]["reunioes"] == 4
    assert r["veredito"].status == "vencedor" and r["veredito"].vencedor == "Consultorias SP"


def test_icp_minimo_de_40_e_janela_de_21_dias(db, world):
    ws = world.a
    a, b = icp(db, ws, "A", "SP"), icp(db, ws, "B", "RJ")
    coorte(db, ws, a, 39, 20, prefix="a")
    coorte(db, ws, b, 60, 2, prefix="b")
    assert q.icp_test(db, ws.id, a.id, b.id, agora=NOW)["veredito"].status == "insuficiente"
    c, d = icp(db, ws, "C", "MG"), icp(db, ws, "D", "BA")
    recente = NOW - timedelta(days=5)
    coorte(db, ws, c, 50, 25, prefix="c", envio=recente, dias_ate_reuniao=2)
    coorte(db, ws, d, 50, 3, prefix="d", envio=recente, dias_ate_reuniao=2)
    assert q.icp_test(db, ws.id, c.id, d.id, agora=NOW)["veredito"].status == "aguardando"  # janela ainda aberta
    assert q.icp_test(db, ws.id, c.id, d.id, agora=NOW, modo_teste=True)["veredito"].status == "vencedor"  # modo teste ignora


def test_reuniao_fora_dos_21_dias_nao_conta(db, world):
    ws = world.a
    a, b = icp(db, ws, "A", "SP"), icp(db, ws, "B", "RJ")
    coorte(db, ws, a, 50, 25, prefix="a", dias_ate_reuniao=30)  # reuniões tarde demais
    coorte(db, ws, b, 50, 5, prefix="b", dias_ate_reuniao=3)
    r = q.icp_test(db, ws.id, a.id, b.id, agora=NOW)
    assert r["a"]["reunioes"] == 0 and r["b"]["reunioes"] == 5
    assert r["veredito"].status in ("empate", "vencedor") and r["veredito"].vencedor != "A"


def test_empate_nao_declara_vencedor(db, world):
    ws = world.a
    a, b = icp(db, ws, "A", "SP"), icp(db, ws, "B", "RJ")
    coorte(db, ws, a, 50, 8, prefix="a")
    coorte(db, ws, b, 50, 9, prefix="b")
    assert q.icp_test(db, ws.id, a.id, b.id, agora=NOW)["veredito"].status == "empate"


# ---------- A/B ----------
def test_ab_por_variante(db, world):
    ws = world.a
    coorte(db, ws, None, 40, 0, prefix="va", variante="A", respostas=14)
    coorte(db, ws, None, 40, 0, prefix="vb", variante="B", respostas=3)
    ab = q.ab_variantes(db, ws.id)
    assert [(g, n, r) for g, n, r, _ in ab["grupos"]] == [("A", 40, 14), ("B", 40, 3)]
    assert ab["veredito"].status == "vencedor" and ab["veredito"].vencedor == "variante A"


# ---------- 4 visões ----------
def cenario_completo(db, ws):
    a, b = icp(db, ws, "Consultorias SP", "SP"), icp(db, ws, "Clínicas RJ", "RJ")
    coorte(db, ws, a, 45, 12, prefix="a", variante="A", respostas=15)
    coorte(db, ws, b, 45, 3, prefix="b", variante="B", respostas=5)
    for ct in db.scalars(select(Contact).limit(3)):
        ct.bloqueado_envio = False
    co = db.scalar(select(Company))
    sg = OpportunitySignal(workspace_id=ws.id, company_id=co.id, tipo="vaga", titulo="v", fonte="f", url="https://x.example", confianca=0.7, hash="h")
    of = Offer(workspace_id=ws.id, nome="Vídeo", tags=["video"], tipos_sinal=["vaga"], cases=[])
    db.add_all([sg, of])
    db.flush()
    ct = db.scalar(select(Contact))
    h = Hypothesis(workspace_id=ws.id, contact_id=ct.id, company_id=co.id, offer_id=of.id, signal_id=sg.id, texto="t", justificativa="j", nota=60, status="aprovada")
    db.add(h)
    db.flush()
    for m in db.scalars(select(OutboundMessage).where(OutboundMessage.contact_id == ct.id)):
        m.hypothesis_id = h.id
    db.add(JobRun(workspace_id=ws.id, tipo="validacao_emails", parametros_hash="x", parametros={}, status="concluido", custo_usd=0.9))
    db.commit()
    return a, b


def test_painel_completo_com_dados_simulados(logged, db, world):
    a, b = cenario_completo(db, world.a)
    aq, ab, cv, it = (f(db, world.a.id) for f in (q.aquisicao, q.abordagem, q.conversao, q.inteligencia))
    assert aq["empresas"] == 90 and aq["validos"] == 90 and aq["pct_validos"] == 100.0 and aq["custo_usd"] == 0.9
    assert aq["custo_por_valido"] == pytest.approx(0.01)
    assert ab["enviadas"] == 90 and ab["taxa_abertura"] is None and "não é confiável" in ab["aviso_abertura"]
    assert ab["respostas"] == 20 and ab["taxa_resposta"] == pytest.approx(22.2)
    assert cv["reunioes"] == 15 and cv["funil"][0] == ("Abordados", 90) and cv["funil"][3] == ("Reuniões", 15)
    assert it["pct_aprovacao"] == 100.0 and it["por_sinal"][0][0] == "vaga"
    html = logged.get("/metricas").text
    for trecho in ("Aquisição", "Abordagem", "Conversão", "Inteligência", "não é confiável", "Funil" if False else "Abordados", "Comparação A/B"):
        assert trecho in html
    html = logged.get(f"/metricas?a={a.id}&b={b.id}&teste=sim").text
    assert "Veredito" in html and "Consultorias SP venceu" in html
    assert "Dados insuficientes" not in html


def test_painel_vazio_nao_quebra(logged):
    r = logged.get("/metricas")
    assert r.status_code == 200 and "Sem dados ainda" in r.text


def test_teste_ignora_icp_de_outro_workspace(client, db, world):
    other = icp(db, world.b, "Segredo B", "SP")
    mine = icp(db, world.a, "Meu", "SP")
    login(client, world.user_a.email)
    html = client.get(f"/metricas?a={mine.id}&b={other.id}").text
    assert "Segredo B" not in html and "Veredito" not in html


def test_metricas_isoladas(client, db, world):
    cenario_completo(db, world.b)
    login(client, world.user_a.email)
    aq = q.aquisicao(db, world.a.id)
    assert aq["empresas"] == 0 and q.conversao(db, world.a.id)["reunioes"] == 0


# ---------- relatório semanal ----------
def test_relatorio_semanal(db, world):
    ws = world.a
    cenario_completo(db, ws)
    txt = q.weekly_report(db, ws.id, NOW)
    assert "Resumo semanal" in txt and "Reuniões marcadas" in txt and "Esperando você" in txt and ws.nome in txt
    assert q.weekly_report(db, world.b.id, NOW).count("•") == 5


def test_relatorio_por_email_vai_para_admins(db, world, monkeypatch):
    enviados = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def send_message(self, msg): enviados.append(msg)
    monkeypatch.setattr(q.smtplib, "SMTP", FakeSMTP)
    assert q.send_weekly_report(db, world.a.id) == 1
    assert enviados[0]["To"] == "a@teste.com" and "Resumo" in enviados[0]["Subject"]
