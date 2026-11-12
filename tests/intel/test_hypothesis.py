"""Etapa 5b: hipótese só existe com sinal + oferta compatível + evidência; aprovação humana obrigatória."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.core.models import Company, Contact, Department, Hypothesis, Offer, OpportunitySignal
from app.intel.hypothesis import decide, generate, run_hypotheses, safe_rewrite
from tests.conftest import login, token
from tests.filtro.test_enrichment import FakeLLM

FUTURO = lambda: datetime.now(timezone.utc) + timedelta(days=20)  # noqa: E731


def cenario(db, ws, *, com_sinal=True, tipo="vaga", status="valido", url="https://jornal.example/v", dept="RH e Gente"):
    c = Company(workspace_id=ws.id, razao_social="ALFA LTDA", nome_fantasia="Alfa Consultoria", dominio="alfa.com.br")
    db.add(c)
    db.flush()
    d = db.scalar(select(Department).where(Department.workspace_id == ws.id, Department.nome == dept))
    ct = Contact(workspace_id=ws.id, company_id=c.id, email="ana@alfa.com.br", email_status=status, department_id=d.id if d else None,
                 nome_completo="Ana Lima", nota_contato=70, nota_final=60)
    db.add(ct)
    if com_sinal:
        db.add(OpportunitySignal(workspace_id=ws.id, company_id=c.id, tipo=tipo, titulo="3 vaga(s) aberta(s) no site", fonte="pagina_trabalhe_conosco",
                                 url=url, confianca=0.7, hash="h", valido_ate=FUTURO()))
    db.commit()
    return c, ct


def oferta(db, ws, **kw):
    o = Offer(workspace_id=ws.id, nome=kw.pop("nome", "Vídeo de recrutamento"), tags=kw.pop("tags", ["video"]),
              tipos_sinal=kw.pop("tipos_sinal", ["vaga"]), departamentos=kw.pop("departamentos", []), cases=[], **kw)
    db.add(o)
    db.commit()
    return o


def test_com_sinal_e_oferta_compativel_gera_hipotese_pendente_com_prova(db, world):
    c, ct = cenario(db, world.a)
    oferta(db, world.a)
    res = generate(db, world.a.id)
    db.commit()
    assert res["hipoteses"] == 1 and res["sem_hipotese"] == 0
    h = db.scalar(select(Hypothesis))
    assert h.status == "pendente" and h.contact_id == ct.id and h.nota > 0
    assert "Alfa Consultoria" in h.texto and "3 vaga(s)" in h.texto and "Vídeo de recrutamento" in h.texto
    assert h.evidencias[0]["url"] == "https://jornal.example/v"
    assert "Marca empregadora" in h.texto  # necessidade do departamento RH ligada à tag "video"


def test_sem_sinal_nao_gera(db, world):
    cenario(db, world.a, com_sinal=False)
    oferta(db, world.a)
    assert generate(db, world.a.id)["hipoteses"] == 0
    assert db.scalar(select(func.count()).select_from(Hypothesis)) == 0


def test_sem_oferta_compativel_nao_gera(db, world):
    cenario(db, world.a, tipo="feira")
    oferta(db, world.a, tipos_sinal=["vaga"])
    res = generate(db, world.a.id)
    assert res["hipoteses"] == 0 and res["sem_hipotese"] == 1
    oferta(db, world.a, nome="Só marketing", tipos_sinal=["feira"], departamentos=["Marketing e Comunicação"])
    assert generate(db, world.a.id)["hipoteses"] == 0  # oferta restrita a outro departamento


def test_sinal_vencido_ou_sem_link_ou_negativo_nao_conta(db, world):
    c, _ = cenario(db, world.a)
    oferta(db, world.a)
    sg = db.scalar(select(OpportunitySignal))
    sg.valido_ate = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()
    assert generate(db, world.a.id)["hipoteses"] == 0
    sg.valido_ate, sg.url = FUTURO(), "javascript:alert(1)"
    db.commit()
    assert generate(db, world.a.id)["hipoteses"] == 0
    sg.url, sg.polaridade = "https://ok.example/x", "negativo"
    db.commit()
    assert generate(db, world.a.id)["hipoteses"] == 0


@pytest.mark.parametrize("status", ["invalido", "bloqueado", "nao_verificado"])
def test_contato_sem_email_utilizavel_nao_ganha_hipotese(db, world, status):
    cenario(db, world.a, status=status)
    oferta(db, world.a)
    assert generate(db, world.a.id)["hipoteses"] == 0


def test_contador_e_contato_bloqueado_ficam_de_fora(db, world):
    c, ct = cenario(db, world.a)
    oferta(db, world.a)
    ct.bloqueado_envio = True
    db.commit()
    assert generate(db, world.a.id)["hipoteses"] == 0
    ct.bloqueado_envio, c.contador = False, True
    db.commit()
    assert generate(db, world.a.id)["hipoteses"] == 0


def test_nao_duplica_ao_gerar_de_novo(db, world):
    cenario(db, world.a)
    oferta(db, world.a)
    generate(db, world.a.id)
    db.commit()
    assert generate(db, world.a.id)["hipoteses"] == 0
    assert db.scalar(select(func.count()).select_from(Hypothesis)) == 1


def test_ia_so_reescreve_se_nao_inventar_fatos(db, world):
    cenario(db, world.a)
    oferta(db, world.a)
    llm = FakeLLM({"Alfa Consultoria": "A Alfa Consultoria está contratando, momento ideal para um vídeo de recrutamento."})
    generate(db, world.a.id, llm)
    db.commit()
    assert db.scalar(select(Hypothesis.texto)).startswith("A Alfa Consultoria está contratando")
    db.execute(Hypothesis.__table__.delete())
    db.commit()
    ruim = FakeLLM({"Alfa Consultoria": "Alfa fez 300% de crescimento, veja https://x.com"})
    generate(db, world.a.id, ruim)
    db.commit()
    assert "300%" not in db.scalar(select(Hypothesis.texto))
    assert safe_rewrite("tem 3 vagas", "tem 3 vagas abertas") and not safe_rewrite("tem 3 vagas", "tem 30 vagas")
    assert not safe_rewrite("x", "veja www.site.com")


def test_aprovar_e_rejeitar_registra_quem_decidiu(db, world):
    cenario(db, world.a)
    oferta(db, world.a)
    generate(db, world.a.id)
    db.commit()
    h = db.scalar(select(Hypothesis))
    decide(db, world.a.id, h.id, world.user_a.id, True, "Texto ajustado por mim")
    db.commit()
    db.refresh(h)
    assert h.status == "aprovada" and h.texto == "Texto ajustado por mim" and h.decidido_por == world.user_a.id and h.decidido_em
    with pytest.raises(LookupError):
        decide(db, world.b.id, h.id, world.user_b.id, True)  # outro workspace não decide


def test_job_idempotente(db, world):
    cenario(db, world.a)
    oferta(db, world.a)
    r1 = run_hypotheses(db, world.a.id, use_default_llm=False)
    assert r1["hipoteses"] == 1
    assert run_hypotheses(db, world.a.id, use_default_llm=False).get("repetido") is True


# ---------- telas ----------
def test_ofertas_crud_e_validacao(logged, db, world):
    html = logged.get("/inteligencia/ofertas").text
    assert "Nova oferta" in html
    tk = token(logged, "/inteligencia/ofertas")
    r = logged.post("/inteligencia/ofertas", data={"nome": "", "csrf_token": tk})
    assert r.status_code == 422 and "Informe o nome" in r.text and "ao menos um tipo de sinal" in r.text
    r = logged.post("/inteligencia/ofertas", data={"nome": "Podcast", "tipos_sinal": ["lancamento"], "tags": "audio, video",
                                                  "cases": "Case 1\nCase 2", "ativo": "sim", "csrf_token": tk})
    assert r.status_code == 303
    o = db.scalar(select(Offer).where(Offer.nome == "Podcast"))
    assert o.tags == ["audio", "video"] and o.cases == ["Case 1", "Case 2"] and o.tipos_sinal == ["lancamento"]
    r = logged.post("/inteligencia/ofertas", data={"nome": "Podcast", "tipos_sinal": ["feira"], "csrf_token": tk})
    assert r.status_code == 422 and "Já existe" in r.text
    r = logged.post("/inteligencia/ofertas/modelos", data={"csrf_token": tk})
    assert r.status_code == 303 and db.scalar(select(func.count()).select_from(Offer)) >= 7
    assert logged.post(f"/inteligencia/ofertas/{o.id}/excluir", data={"csrf_token": tk}).status_code == 303


def test_fila_e_top50_com_prova_clicavel(logged, db, world):
    cenario(db, world.a)
    oferta(db, world.a)
    generate(db, world.a.id)
    db.commit()
    home = logged.get("/inteligencia").text
    assert 'href="https://jornal.example/v"' in home and "Alfa Consultoria" in home
    fila = logged.get("/inteligencia/hipoteses").text
    assert "Aprovar" in fila and "Rejeitar" in fila
    h = db.scalar(select(Hypothesis))
    r = logged.post(f"/inteligencia/hipoteses/{h.id}/decidir", data={"acao": "aprovar", "texto": "", "csrf_token": token(logged, "/inteligencia/hipoteses")})
    assert r.status_code == 303
    db.refresh(h)
    assert h.status == "aprovada"
    assert "Alfa Consultoria" in logged.get("/inteligencia/hipoteses?status=aprovada").text


def test_operador_nao_aprova_mas_aprovador_sim(client, db, world):
    cenario(db, world.a)
    oferta(db, world.a)
    generate(db, world.a.id)
    db.commit()
    h = db.scalar(select(Hypothesis))
    login(client, world.operador_a.email)
    tk = token(client, "/inteligencia")
    assert client.post(f"/inteligencia/hipoteses/{h.id}/decidir", data={"acao": "aprovar", "csrf_token": tk}).status_code == 403
    client.post("/logout", data={"csrf_token": tk})
    login(client, world.approver_a.email)
    tk = token(client, "/inteligencia")
    assert client.post(f"/inteligencia/hipoteses/{h.id}/decidir", data={"acao": "aprovar", "csrf_token": tk}).status_code == 303


def test_hipoteses_isoladas_entre_workspaces(client, db, world):
    cenario(db, world.b)
    oferta(db, world.b)
    generate(db, world.b.id)
    db.commit()
    h = db.scalar(select(Hypothesis))
    login(client, world.user_a.email)
    assert "Alfa Consultoria" not in client.get("/inteligencia/hipoteses").text
    assert client.post(f"/inteligencia/hipoteses/{h.id}/decidir", data={"acao": "aprovar", "csrf_token": token(client, "/inteligencia")}).status_code == 404
