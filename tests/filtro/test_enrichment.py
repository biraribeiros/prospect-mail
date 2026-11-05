"""Etapa 5a: nomes em cascata, validação cruzada, 4 notas, nota de vida e setor."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select

from app.core.llm import ClaudeClient, cached_complete, extract_json
from app.core.models import ApiCache, Company, Contact, ContactRole, Evidence, OpportunitySignal
from app.filtro.enrichment import names as nm
from app.filtro.enrichment import scores as sc
from app.filtro.enrichment.service import run_enrichment
from app.filtro.validation.verifiers import VerifierResult
from tests.filtro.test_validation import FakeVerifier
from tests.hunter.test_signals import fetcher_for


class FakeLLM:
    model = "fake-model"

    def __init__(self, respostas=None):
        self.respostas, self.chamadas = respostas or {}, []

    def complete(self, system, user, max_tokens=800):
        self.chamadas.append(user)
        for k, v in self.respostas.items():
            if k in user:
                return v, 0.0004
        return '{"nome": null, "certeza": 0}', 0.0004


# ---------- nomes ----------
@pytest.mark.parametrize("email,nome", [("maria.silva@x.com", "Maria Silva"), ("joao_pedro@x.com", "Joao Pedro"),
                                        ("patricia@x.com", "Patricia"), ("msilva@x.com", None), ("contato@x.com", None),
                                        ("comercial@x.com", None), ("ze@x.com", None), ("ana.b2@x.com", None),
                                        ("a.silva@x.com", None)])
def test_nome_pela_parte_local(email, nome):
    g = nm.name_from_local(email)
    assert (g.nome if g else None) == nome


def test_nome_pelo_email_combina_com_nome():
    assert nm.local_matches_name("maria.silva", "Maria da Silva")
    assert nm.local_matches_name("msilva", "Maria Silva")
    assert nm.local_matches_name("maria", "Maria Silva")
    assert not nm.local_matches_name("carlos", "Maria Silva")


def test_padroes_de_email_para_socio():
    c = nm.email_candidates("Maria da Silva", "alfa.com.br")
    assert c[:3] == ["maria@alfa.com.br", "maria.silva@alfa.com.br", "msilva@alfa.com.br"]
    assert nm.email_candidates("Maria", "alfa.com.br") == ["maria@alfa.com.br"]
    assert nm.email_candidates("Maria Silva", "") == []


def test_pagina_de_equipe():
    html = ("<div><h3>Carlos Souza</h3><p>Diretor Comercial</p></div><div><p>Ana Paula Lima — Gerente de Marketing</p></div>"
            "<div><p>Nossa história começou em 1990</p></div>")
    assert nm.team_members(html) == [("Carlos Souza", "Diretor Comercial"), ("Ana Paula Lima", "Gerente de Marketing")]


# ---------- cliente de IA ----------
def test_extract_json_e_cache_de_ia(db, world):
    assert extract_json('blá {"a": 1} blá') == {"a": 1} and extract_json("sem json") is None and extract_json("{ruim") is None
    llm = FakeLLM({"x": '{"ok": true}'})
    t1, c1, hit1 = cached_complete(db, world.a.id, llm, "sys", "x", 100)
    t2, c2, hit2 = cached_complete(db, world.a.id, llm, "sys", "x", 100)
    assert (hit1, hit2) == (False, True) and c1 > 0 and c2 == 0 and len(llm.chamadas) == 1


def test_claude_client_calcula_custo():
    def handler(req):
        body = req.read().decode()
        assert '"system"' in body and req.headers["x-api-key"] == "K"
        return httpx.Response(200, json={"content": [{"type": "text", "text": "oi"}], "usage": {"input_tokens": 1000, "output_tokens": 500}})
    c = ClaudeClient("K", "claude-haiku-5-5", 1.0, 5.0, httpx.Client(transport=httpx.MockTransport(handler)))
    text, cost = c.complete("sys", "user")
    assert text == "oi" and cost == pytest.approx((1000 * 1.0 + 500 * 5.0) / 1e6)


# ---------- notas ----------
def test_notas_sao_independentes_e_a_final_e_ponderada():
    assert sc.nota_email("valido", "pessoal", True) == 100 and sc.nota_email("invalido", "pessoal", True) == 0
    assert sc.nota_email("arriscado", "generico", False) == 30
    assert sc.nota_contato(sc.ContactFacts()) == 0
    alta = sc.nota_contato(sc.ContactFacts(True, 0.9, True, True, True, 100))
    assert alta >= 90
    assert sc.nota_empresa(sc.CompanyFacts()) == 0
    assert sc.nota_empresa(sc.CompanyFacts(True, True, True, True, 100, 5)) == 100
    assert sc.nota_final(100, 100, 100, 100) == 100 and sc.nota_final(0, 0, 100, 0) == 30
    agora = datetime.now(timezone.utc)
    assert sc.nota_oportunidade([]) == 0
    um = sc.nota_oportunidade([(0.65, "positivo", agora + timedelta(days=5))])
    dois = sc.nota_oportunidade([(0.65, "positivo", agora + timedelta(days=5))] * 2)
    assert 0 < um < dois
    assert sc.nota_oportunidade([(0.9, "positivo", agora - timedelta(days=1))]) == 0  # vencido não conta
    assert sc.nota_oportunidade([(0.9, "positivo", None), (0.5, "negativo", None)]) < sc.nota_oportunidade([(0.9, "positivo", None)])


def test_nota_de_vida_e_setor():
    assert sc.liveness(False, False, 0, False, 0) == 0
    assert sc.liveness(True, True, 3, True, 2) == 90  # 30 site + 20 Maps + 24 redes + 6 telefone + 10 sinais
    assert sc.setor_humano("70.20-4-00") == "Consultoria empresarial" and sc.setor_humano(None) is None
    assert sc.gatilhos("Educação")[0] == "período de matrículas" and sc.gatilhos("Inexistente")


# ---------- fluxo ----------
def setup_base(db, ws):
    c = Company(workspace_id=ws.id, cnpj="11222333000181", razao_social="ALFA LTDA", nome_fantasia="Alfa Consultoria",
                dominio="alfa.com.br", site="https://alfa.com.br", site_status="ok", telefone="1933334444",
                situacao_cadastral="Ativa", cnae_principal="7020-4/00", municipio="Campinas", uf="SP")
    db.add(c)
    db.flush()
    db.add_all([Contact(workspace_id=ws.id, company_id=c.id, email="maria.silva@alfa.com.br", email_status="valido",
                        email_type="pessoal"),
                Contact(workspace_id=ws.id, company_id=c.id, email="carlos@alfa.com.br", email_status="valido", email_type="pessoal"),
                Contact(workspace_id=ws.id, company_id=c.id, email="msilva2@alfa.com.br", email_status="arriscado", email_type="pessoal"),
                Contact(workspace_id=ws.id, company_id=c.id, email="contato@alfa.com.br", email_status="arriscado", email_type="generico")])
    db.commit()
    return c


def test_nome_preenchido_em_70_por_cento_dos_validos(db, world):
    ws = world.a
    c = Company(workspace_id=ws.id, razao_social="X", dominio="x.com.br")
    db.add(c)
    db.flush()
    resolviveis = ["ana.souza", "bruno.lima", "carla.dias", "daniel", "eduarda.reis", "fabio.melo", "gustavo.rocha", "helena.cruz"]
    for n in resolviveis + ["msilva", "jsouza"]:  # 8 de 10 saem pela parte local; 2 são ambíguos
        db.add(Contact(workspace_id=ws.id, company_id=c.id, email=f"{n}@x.com.br", email_status="valido", email_type="pessoal"))
    db.commit()
    res = run_enrichment(db, ws.id, llm=None, use_default_llm=False, verifier=None, use_default_verifier=False)
    validos = db.scalars(select(Contact).where(Contact.workspace_id == ws.id, Contact.email_status == "valido")).all()
    com_nome = [v for v in validos if v.nome_completo]
    assert len(validos) == 10 and len(com_nome) / len(validos) >= 0.7 and res["contatos"] == 10


def test_enriquecimento_grava_nome_origem_evidencia_e_notas(db, world):
    ws = world.a
    c = setup_base(db, ws)
    db.add(OpportunitySignal(workspace_id=ws.id, company_id=c.id, tipo="expansao", titulo="Alfa expande", fonte="google_news",
                             url="https://jornal.example/a", confianca=0.65, hash="h",
                             valido_ate=datetime.now(timezone.utc) + timedelta(days=30)))
    db.commit()
    res = run_enrichment(db, ws.id, llm=None, use_default_llm=False, verifier=None, use_default_verifier=False)
    assert res["contatos"] == 4 and res["nomes_novos"] == 2  # maria.silva e carlos
    maria = db.scalar(select(Contact).where(Contact.email == "maria.silva@alfa.com.br"))
    assert maria.nome_completo == "Maria Silva" and maria.primeiro_nome == "Maria" and maria.nome_origem == "email_local"
    ev = db.scalars(select(Evidence).where(Evidence.entidade_id == maria.id, Evidence.entidade_tipo == "contact",
                                           Evidence.campo == "nome_completo")).all()
    assert {e.fonte for e in ev} >= {"email_local", "validacao_cruzada"}
    assert max(float(e.confianca) for e in ev) > 0.4  # a validação cruzada subiu a confiança
    for ct in db.scalars(select(Contact)):
        assert all(getattr(ct, f) is not None for f in ("nota_empresa", "nota_contato", "nota_email", "nota_oportunidade", "nota_final"))
    contato = db.scalar(select(Contact).where(Contact.email == "contato@alfa.com.br"))
    assert maria.nota_email > contato.nota_email and maria.nota_contato > contato.nota_contato
    assert maria.nota_oportunidade > 0 and maria.nota_final > contato.nota_final
    db.refresh(c)
    assert c.setor_humano == "Consultoria empresarial" and c.gatilhos_previaveis and c.liveness_score > 0


def test_ia_so_para_o_que_sobrou_e_com_custo_registrado(db, world):
    ws = world.a
    setup_base(db, ws)
    llm = FakeLLM({"msilva2@alfa.com.br": '{"nome": "Marcos Silva", "certeza": 0.9}'})
    res = run_enrichment(db, ws.id, llm=llm, verifier=None, use_default_verifier=False)
    ct = db.scalar(select(Contact).where(Contact.email == "msilva2@alfa.com.br"))
    assert ct.nome_completo == "Marcos Silva" and ct.nome_origem == "ia"
    ev = db.scalar(select(Evidence).where(Evidence.entidade_id == ct.id, Evidence.campo == "nome_completo", Evidence.fonte == "ia"))
    assert float(ev.confianca) <= 0.5  # nome de IA nunca ganha confiança alta
    assert res["custo_usd"] > 0
    assert not any("maria.silva" in u for u in llm.chamadas)  # quem as regras resolveram não vai para a IA
    assert db.scalar(select(func.count()).select_from(ApiCache).where(ApiCache.provedor == "llm")) >= 1


def test_ia_com_resposta_ruim_ou_incerta_nao_inventa_nome(db, world):
    ws = world.a
    setup_base(db, ws)
    llm = FakeLLM({"msilva2@alfa.com.br": '{"nome": "Marcos <script>", "certeza": 0.95}'})
    run_enrichment(db, ws.id, llm=llm, verifier=None, use_default_verifier=False)
    assert db.scalar(select(Contact.nome_completo).where(Contact.email == "msilva2@alfa.com.br")) is None
    llm2 = FakeLLM({"msilva2@alfa.com.br": '{"nome": "Marcos Silva", "certeza": 0.3}'})
    db.execute(Contact.__table__.update().values(nota_final=None))
    db.commit()
    run_enrichment(db, ws.id, llm=llm2, verifier=None, use_default_verifier=False)
    assert db.scalar(select(Contact.nome_completo).where(Contact.email == "msilva2@alfa.com.br")) is None


def test_pagina_de_equipe_da_nome_e_cargo(db, world):
    ws = world.a
    c = setup_base(db, ws)
    f = fetcher_for({"/equipe": "<div><h3>Carlos Souza</h3><p>Diretor Comercial</p></div>"})
    run_enrichment(db, ws.id, llm=None, use_default_llm=False, fetcher=f, verifier=None, use_default_verifier=False)
    carlos = db.scalar(select(Contact).where(Contact.email == "carlos@alfa.com.br"))
    assert carlos.nome_completo == "Carlos Souza" and carlos.cargo == "Diretor Comercial" and carlos.nome_origem == "pagina_equipe"
    assert db.scalar(select(ContactRole.papel).where(ContactRole.contact_id == carlos.id)) == "decisor"


def test_email_de_socio_so_e_adotado_se_o_verificador_confirma(db, world):
    ws = world.a
    c = setup_base(db, ws)
    socio = Contact(workspace_id=ws.id, company_id=c.id, nome_completo="Ana Paula Lima", origem="cnpj_receita")
    db.add(socio)
    db.commit()
    ver = FakeVerifier({"ana@alfa.com.br": "invalido", "ana.lima@alfa.com.br": "valido"})
    res = run_enrichment(db, ws.id, llm=None, use_default_llm=False, verifier=ver, use_default_verifier=False)
    db.refresh(socio)
    assert socio.email == "ana.lima@alfa.com.br" and socio.email_status == "valido" and res["emails_de_socios"] == 1
    assert ver.chamadas == ["ana@alfa.com.br", "ana.lima@alfa.com.br"]  # parou no primeiro válido
    # sem verificador, nada é inventado
    outro = Contact(workspace_id=ws.id, company_id=c.id, nome_completo="Pedro Alves", origem="cnpj_receita")
    db.add(outro)
    db.commit()
    run_enrichment(db, ws.id, llm=None, use_default_llm=False, verifier=None, use_default_verifier=False)
    db.refresh(outro)
    assert outro.email is None


def test_catch_all_nao_adota_email_de_socio(db, world):
    ws = world.a
    c = setup_base(db, ws)
    socio = Contact(workspace_id=ws.id, company_id=c.id, nome_completo="Ana Lima", origem="cnpj_receita")
    db.add(socio)
    db.commit()

    class CatchAll(FakeVerifier):
        def verify(self, email):
            self.chamadas.append(email)
            return VerifierResult("valido", True, 0.001, "catch-all")
    run_enrichment(db, ws.id, llm=None, use_default_llm=False, verifier=CatchAll(), use_default_verifier=False)
    db.refresh(socio)
    assert socio.email is None


def test_job_idempotente_e_isolamento_entre_workspaces(db, world):
    setup_base(db, world.a)
    db.add(Contact(workspace_id=world.b.id, email="joana.reis@b.com.br", email_status="valido"))
    db.commit()
    r1 = run_enrichment(db, world.a.id, llm=None, use_default_llm=False, verifier=None, use_default_verifier=False)
    assert r1["contatos"] == 4
    assert db.scalar(select(Contact.nome_completo).where(Contact.email == "joana.reis@b.com.br")) is None  # outro workspace intacto
    r2 = run_enrichment(db, world.a.id, llm=None, use_default_llm=False, verifier=None, use_default_verifier=False)
    assert r2.get("repetido") is True


def test_tela_mostra_notas(logged, db, world):
    setup_base(db, world.a)
    run_enrichment(db, world.a.id, llm=None, use_default_llm=False, verifier=None, use_default_verifier=False)
    html = logged.get("/filtro").text
    assert "Maria Silva" in html and "Enriquecer contatos" in html
