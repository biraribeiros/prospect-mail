"""Etapa 7: mensagens únicas, baseadas em fatos, com regras de bloqueio, anti-spam e aprovação em lote."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.core.models import Company, Contact, Department, Hypothesis, IcpProfile, Offer, OpportunitySignal, OutboundMessage
from app.disparo.compose import (approve_sequence, caps_ratio, check_rules, compose_for, count_words, discard_sequence, fill,
                                 jaccard, local_spam_score, most_diverse, rspamd_score, run_compose, spam_check, Draft)
from tests.conftest import login, token
from tests.filtro.test_enrichment import FakeLLM

OK = lambda raw: 1.2  # noqa: E731  (Rspamd simulado: passa)


def hip(db, ws, i, nome="Ana", status="aprovada", tipo="vaga", oferta=None, cases=None, empresa=None):
    c = Company(workspace_id=ws.id, razao_social=f"EMPRESA {i} LTDA", nome_fantasia=empresa or f"Empresa {i}", dominio=f"e{i}.com.br")
    db.add(c)
    db.flush()
    d = db.scalar(select(Department).where(Department.workspace_id == ws.id, Department.nome == "RH e Gente"))
    ct = Contact(workspace_id=ws.id, company_id=c.id, email=f"p{i}@e{i}.com.br", email_status="valido", primeiro_nome=nome,
                 department_id=d.id if d else None)
    db.add(ct)
    sg = OpportunitySignal(workspace_id=ws.id, company_id=c.id, tipo=tipo, titulo="3 vaga(s) aberta(s) no site", fonte="pagina", url=f"https://e{i}.com.br/vagas",
                           confianca=0.7, hash=f"h{i}", valido_ate=datetime.now(timezone.utc) + timedelta(days=20))
    db.add(sg)
    if oferta is None:
        oferta = db.scalar(select(Offer).where(Offer.workspace_id == ws.id)) or Offer(
            workspace_id=ws.id, nome="Vídeo de recrutamento", descricao="Vídeos de marca empregadora", tags=["video"], tipos_sinal=["vaga"],
            cases=cases if cases is not None else [])
        db.add(oferta)
    db.flush()
    h = Hypothesis(workspace_id=ws.id, contact_id=ct.id, company_id=c.id, offer_id=oferta.id, signal_id=sg.id, texto="t", justificativa="j",
                   nota=70, status=status, evidencias=[{"tipo": "sinal", "url": sg.url, "fonte": "pagina"}])
    db.add(h)
    db.commit()
    return h


# ---------- regras ----------
def test_fill_e_variavel_vazia():
    t, faltam = fill("Olá {{primeiro_nome}}, {{empresa}}{{prova}}", {"primeiro_nome": "", "empresa": "Alfa", "prova": ""})
    assert faltam == ["primeiro_nome"] and "{{primeiro_nome}}" in t and t.endswith("Alfa")  # prova é opcional


def test_regras_de_bloqueio():
    bom = "Olá, Ana. Vi que a Alfa está contratando. Posso te mostrar uma ideia em 15 minutos?"
    assert check_rules("Uma ideia", bom) == []
    assert "variável sem preencher" in check_rules("Oi {{nome}}", bom)
    assert any("150 palavras" in e for e in check_rules("x", "palavra " * 160))
    assert any("link" in e for e in check_rules("x", bom + " veja https://a.com e https://b.com"))
    assert check_rules("x", bom + " veja https://a.com") == []  # 1 link é permitido
    assert any("imagem" in e for e in check_rules("x", bom + ' <img src="a.png">'))
    assert any("MAIÚSCULAS" in e for e in check_rules("x", "COMPRE AGORA ESTA OFERTA INCRÍVEL HOJE MESMO PARA VOCÊ ok"))
    assert any("spam" in e for e in check_rules("x", bom + " é grátis, clique aqui!!!"))
    assert any("assunto" in e for e in check_rules("", bom))
    assert count_words("um dois três") == 3 and caps_ratio("ABC def") == 0.5


def test_spam_local_e_rspamd(monkeypatch):
    assert local_spam_score("Uma ideia", "Olá, Ana. Posso ajudar?") < 5
    assert local_spam_score("GRÁTIS 100%", "Clique aqui é grátis!!! URGENTE") >= 5
    s, origem = spam_check("a", "b", 1, checker=lambda raw: 2.5)
    assert (s, origem) == (2.5, "rspamd")
    s, origem = spam_check("a", "b", 1, checker=lambda raw: None)
    assert origem == "local"
    import httpx
    ok = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"score": 3.4})))
    assert rspamd_score(b"x", "http://rspamd:11333", ok) == 3.4
    down = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert rspamd_score(b"x", "http://rspamd:11333", down) is None


def test_escolhe_a_variacao_mais_diferente():
    a, b, c = Draft("a", "olá ana vimos vagas na empresa"), Draft("b", "bom dia equipe notamos expansão recente"), Draft("c", "oi tudo bem")
    assert most_diverse([a, b, c], []) is a
    assert most_diverse([a, b, c], ["olá ana vimos vagas na empresa hoje"]) is not a
    assert jaccard("a b c", "a b c") == 1.0 and jaccard("", "") == 0.0


# ---------- geração ----------
def test_sem_hipotese_aprovada_nao_gera(db, world):
    h = hip(db, world.a, 1, status="pendente")
    with pytest.raises(ValueError):
        compose_for(db, world.a.id, h, checker=OK)
    h2 = hip(db, world.a, 2, status="rejeitada")
    res = run_compose(db, world.a.id, use_default_llm=False, checker=OK)
    assert res.get("criadas", 0) == 0 and db.scalar(select(func.count()).select_from(OutboundMessage)) == 0
    assert h2.status == "rejeitada"


def test_gera_sequencia_de_3_passos_com_fatos(db, world):
    h = hip(db, world.a, 1, cases=["Vídeo de recrutamento para a Beta, publicado no LinkedIn"])
    r = compose_for(db, world.a.id, h, checker=OK)
    db.commit()
    assert r["criada"] and not r["bloqueada"]
    msgs = db.scalars(select(OutboundMessage).order_by(OutboundMessage.passo)).all()
    assert [m.passo for m in msgs] == [1, 2, 3] and [m.objetivo for m in msgs] == ["sonda", "seguimento", "ultimo_toque"]
    assert all(m.thread_raiz_id == msgs[0].id for m in msgs) and msgs[0].status == "rascunho"
    p1 = msgs[0]
    assert "Ana" in p1.corpo and "Empresa 1" in p1.corpo and "está contratando" in p1.corpo
    assert "Um exemplo: Vídeo de recrutamento para a Beta" in p1.corpo  # prova só vem do case cadastrado
    assert len(p1.variacoes) == 3 and float(p1.spam_score) == 1.2
    assert msgs[1].assunto.startswith("Re: ") and "último contato" in msgs[2].corpo
    assert count_words(p1.corpo) <= 150
    assert compose_for(db, world.a.id, h, checker=OK)["criada"] is False  # não duplica


def test_sem_case_cadastrado_nao_inventa_prova(db, world):
    h = hip(db, world.a, 1, cases=[])
    compose_for(db, world.a.id, h, checker=OK)
    corpo = db.scalar(select(OutboundMessage.corpo).where(OutboundMessage.passo == 1))
    assert "Um exemplo" not in corpo and "{{" not in corpo


def test_variavel_vazia_bloqueia_na_fila(db, world):
    h = hip(db, world.a, 1, nome="")
    r = compose_for(db, world.a.id, h, checker=OK)
    db.commit()
    assert r["bloqueada"]
    m = db.scalar(select(OutboundMessage).where(OutboundMessage.passo == 1))
    assert m.status == "bloqueada" and "variável vazia: primeiro_nome" in m.motivo
    assert db.scalar(select(OutboundMessage.status).where(OutboundMessage.passo == 2)) == "bloqueada"


def test_nota_de_spam_alta_bloqueia(db, world):
    h = hip(db, world.a, 1)
    compose_for(db, world.a.id, h, checker=lambda raw: 7.5)
    m = db.scalar(select(OutboundMessage).where(OutboundMessage.passo == 1))
    assert m.status == "bloqueada" and "spam alta" in m.motivo


def test_50_mensagens_em_2_icps_todas_abaixo_de_5(db, world):
    ws = world.a
    for i in range(50):
        hip(db, ws, i, nome=["Ana", "Bruno", "Carla", "Diego", "Eva"][i % 5])
    res = run_compose(db, ws.id, use_default_llm=False, checker=OK)
    assert res["criadas"] == 50 and res["bloqueadas"] == 0
    firsts = db.scalars(select(OutboundMessage).where(OutboundMessage.passo == 1)).all()
    assert len(firsts) == 50 and all(float(m.spam_score) < 5 for m in firsts) and all(m.status == "rascunho" for m in firsts)
    assert len({m.assunto for m in firsts}) > 1  # a escolha da variação mais diversa varia os textos
    assert run_compose(db, ws.id, use_default_llm=False, checker=OK).get("criadas") == 0


def test_ia_escreve_variacoes_mas_nao_pode_inventar(db, world):
    h = hip(db, world.a, 1)
    bom = {"variacoes": [{"assunto": f"Ideia {i}", "corpo": f"Olá, Ana. Vi que a Empresa 1 está contratando. Posso mostrar uma ideia {chr(97 + i)}?"}
                         for i in range(3)]}
    llm = FakeLLM({"Empresa 1": json.dumps(bom)})
    compose_for(db, world.a.id, h, llm, OK)
    p1 = db.scalar(select(OutboundMessage).where(OutboundMessage.passo == 1))
    assert p1.assunto.startswith("Ideia") and len(p1.variacoes) == 3
    db.execute(OutboundMessage.__table__.delete())
    db.commit()
    ruim = {"variacoes": [{"assunto": "Oi", "corpo": "A Empresa 1 cresceu 300% e tem 12 clientes. Vamos falar?"}] * 3}
    compose_for(db, world.a.id, h, FakeLLM({"Empresa 1": json.dumps(ruim)}), OK)
    p1 = db.scalar(select(OutboundMessage).where(OutboundMessage.passo == 1))
    assert "300%" not in p1.corpo and "12 clientes" not in p1.corpo  # número inventado: caiu para os modelos


# ---------- aprovação ----------
def test_aprovar_sequencia_com_edicao_e_revalidacao(db, world):
    h = hip(db, world.a, 1)
    compose_for(db, world.a.id, h, checker=OK)
    db.commit()
    first = db.scalar(select(OutboundMessage).where(OutboundMessage.passo == 1))
    erros = approve_sequence(db, world.a.id, first.id, {first.id: ("Oi {{x}}", "texto")}, OK)
    assert erros and any("variável" in e for e in erros)
    assert db.get(OutboundMessage, first.id).status == "bloqueada"
    erros = approve_sequence(db, world.a.id, first.id, {first.id: ("Minha ideia", "Olá, Ana. Vi que vocês estão contratando. Podemos conversar?")}, OK)
    assert erros == []
    seq = db.scalars(select(OutboundMessage).order_by(OutboundMessage.passo)).all()
    assert [m.status for m in seq] == ["aprovada"] * 3 and seq[0].assunto == "Minha ideia"
    assert approve_sequence(db, world.a.id, first.id, None, OK) == ["Esta sequência já foi aprovada ou descartada."]
    with pytest.raises(LookupError):
        approve_sequence(db, world.b.id, first.id, None, OK)


def test_hipotese_revogada_impede_aprovar_mensagem(db, world):
    h = hip(db, world.a, 1)
    compose_for(db, world.a.id, h, checker=OK)
    h.status = "rejeitada"
    db.commit()
    first = db.scalar(select(OutboundMessage).where(OutboundMessage.passo == 1))
    assert "hipótese" in approve_sequence(db, world.a.id, first.id, None, OK)[0]


def test_descartar_sequencia(db, world):
    compose_for(db, world.a.id, hip(db, world.a, 1), checker=OK)
    first = db.scalar(select(OutboundMessage).where(OutboundMessage.passo == 1))
    discard_sequence(db, world.a.id, first.id)
    assert set(db.scalars(select(OutboundMessage.status))) == {"descartada"}


# ---------- telas ----------
def test_tela_aprovacao_em_lote_e_papeis(client, db, world):
    for i in range(3):
        compose_for(db, world.a.id, hip(db, world.a, i), checker=OK)
    db.commit()
    firsts = db.scalars(select(OutboundMessage.id).where(OutboundMessage.passo == 1).order_by(OutboundMessage.id)).all()
    login(client, world.operador_a.email)
    tk = token(client, "/disparo/mensagens")
    html = client.get("/disparo/mensagens").text
    assert "Passo 3" in html and "Aprovar marcadas" not in html  # operador vê, mas não aprova
    assert client.post("/disparo/mensagens/decidir", data={"csrf_token": tk, "acao": "aprovar", "sel": [str(firsts[0])]}).status_code == 403
    client.post("/logout", data={"csrf_token": tk})
    login(client, world.approver_a.email)
    tk = token(client, "/disparo/mensagens")
    assert "Aprovar marcadas" in client.get("/disparo/mensagens").text
    r = client.post("/disparo/mensagens/decidir", data={"csrf_token": tk, "acao": "aprovar", "sel": [str(firsts[0]), str(firsts[1])]})
    assert r.status_code == 303
    assert db.scalar(select(func.count()).select_from(OutboundMessage).where(OutboundMessage.status == "aprovada")) == 6
    r = client.post("/disparo/mensagens/decidir", data={"csrf_token": tk, "acao": "descartar", "sel": [str(firsts[2])]})
    assert db.scalar(select(func.count()).select_from(OutboundMessage).where(OutboundMessage.status == "descartada")) == 3
    r = client.post("/disparo/mensagens/decidir", data={"csrf_token": tk, "acao": "aprovar"})
    assert r.status_code == 303  # nada marcado: aviso, sem erro


def test_modelos_proprios_sao_usados(logged, db, world):
    tk = token(logged, "/disparo/modelos")
    r = logged.post("/disparo/modelos", data={"csrf_token": tk, "nome": "Meu modelo", "objetivo": "sonda",
                                              "assunto": "Oi {{primeiro_nome}}", "corpo": "{{primeiro_nome}}, vi que a {{empresa}} {{frase_sinal}}. {{pedido}}"})
    assert r.status_code == 303 and "Meu modelo" in logged.get("/disparo/modelos").text
    h = hip(db, world.a, 1)
    compose_for(db, world.a.id, h, checker=OK)
    p1 = db.scalar(select(OutboundMessage).where(OutboundMessage.passo == 1))
    assert p1.assunto == "Oi Ana"


def test_mensagens_isoladas_entre_workspaces(client, db, world):
    compose_for(db, world.b.id, hip(db, world.b, 1, empresa="Segredo SA"), checker=OK)
    db.commit()
    login(client, world.user_a.email)
    assert "Segredo SA" not in client.get("/disparo/mensagens").text
    fid = db.scalar(select(OutboundMessage.id).where(OutboundMessage.passo == 1))
    tk = token(client, "/disparo/mensagens")
    client.post("/disparo/mensagens/decidir", data={"csrf_token": tk, "acao": "aprovar", "sel": [str(fid)]})
    assert db.scalar(select(OutboundMessage.status).where(OutboundMessage.passo == 1)) == "rascunho"  # outro workspace: intacto
    assert IcpProfile  # (ICPs ficam para a Etapa 9)
