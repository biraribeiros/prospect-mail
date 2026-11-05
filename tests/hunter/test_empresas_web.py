"""Telas de empresas e coleta: filtros, evidências, estimativa de custo, fila, progresso em tempo real e isolamento."""
import asyncio
import json

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.models import Company, Contact, Evidence, IcpProfile, JobRun
from app.web.routes import empresas as rotas
from app.workers.jobs import collect_cnpj_job, collect_places_job, crawl_sites_job
from tests.conftest import login, token
from tests.hunter.receita import cnpj_de, write_set


@pytest.fixture
def filas(monkeypatch):
    pedidos = []

    async def fake(job, *args, **kw):
        pedidos.append((job, args))

    monkeypatch.setattr(rotas, "enqueue", fake)
    return pedidos


@pytest.fixture
def dados(db, world):
    a = Company(workspace_id=world.a.id, cnpj=cnpj_de("11111111"), razao_social="Alfa Consultoria Ltda",
                nome_fantasia="Alfa", municipio="Patos de Minas", uf="MG", site="https://alfa.com.br",
                dominio="alfa.com.br", site_status="ok")
    b = Company(workspace_id=world.a.id, nome_fantasia="Beta Contabil", municipio="Uberlândia", uf="MG",
                site="https://beta.com.br", dominio="beta.com.br")
    c = Company(workspace_id=world.a.id, razao_social="Gama SA", uf="SP")
    segredo = Company(workspace_id=world.b.id, nome_fantasia="Empresa Secreta do B", uf="RJ")
    db.add_all([a, b, c, segredo])
    db.commit()
    db.add_all([
        Evidence(workspace_id=world.a.id, entidade_tipo="company", entidade_id=a.id, campo="cnpj", valor=a.cnpj,
                 fonte="cnpj_receita", url="https://dadosabertos.rfb.gov.br/CNPJ/", confianca=0.95),
        Evidence(workspace_id=world.a.id, entidade_tipo="company", entidade_id=a.id, campo="telefone", valor="3438221000",
                 fonte="crawler_site", url="javascript:alert(1)", confianca=0.7),
        Evidence(workspace_id=world.a.id, entidade_tipo="company", entidade_id=b.id, campo="google_place_id", valor="p1",
                 fonte="google_places", url="https://maps.google.com/?cid=1", confianca=0.8),
        Contact(workspace_id=world.a.id, company_id=a.id, nome_completo="Maria da Silva", cargo="Sócio-Administrador")])
    db.commit()
    return a, b, c, segredo


def test_lista_de_empresas_e_filtros(logged, dados):
    html = logged.get("/hunter/empresas").text
    assert "3 empresa(s)" in html and "Alfa" in html and "Beta Contabil" in html and "Gama SA" in html
    assert "Empresa Secreta do B" not in html  # de outro workspace
    assert "11.111.111/0001" in html  # CNPJ formatado
    f = lambda q: logged.get("/hunter/empresas?" + q).text
    assert "Alfa" in f("q=alfa") and "Beta" not in f("q=alfa")
    assert "Alfa" in f("q=" + cnpj_de("11111111")[:8])  # busca por CNPJ
    assert "Gama" in f("uf=SP") and "Alfa" not in f("uf=SP")
    assert "Beta" in f("fonte=google_places") and "Alfa" not in f("fonte=google_places")
    assert "Gama" in f("site=sem") and "Alfa" not in f("site=sem")
    assert "Beta" in f("site=nao_lido") and "Alfa" not in f("site=nao_lido")
    assert "Alfa" in f("cnpj=com") and "Beta" not in f("cnpj=com")
    assert "Nenhuma empresa" in f("q=nao-existe") and logged.get("/hunter/empresas?q=%25").status_code == 200


def test_detalhe_mostra_origem_de_cada_dado_e_nao_cria_link_perigoso(logged, dados):
    a = dados[0]
    html = logged.get(f"/hunter/empresas/{a.id}").text
    assert "De onde vem cada dado" in html and "cnpj receita" in html and "crawler site" in html
    assert "Maria da Silva" in html and "95%" in html
    assert 'href="https://dadosabertos.rfb.gov.br/CNPJ/"' in html
    assert "javascript:" not in html  # URL de evidência não-http vira texto, sem link


def test_site_malicioso_nao_vira_link_clicavel(logged, world, db):
    db.add(Company(workspace_id=world.a.id, nome_fantasia="Mal", site="javascript:alert(document.cookie)"))
    db.commit()
    html = logged.get("/hunter/empresas").text
    assert "javascript:alert" not in html.replace("&#34;", '"') or 'href="javascript' not in html
    assert 'href="javascript' not in html and 'href="#"' in html


def test_empresa_de_outro_workspace_da_404(logged, dados):
    assert logged.get(f"/hunter/empresas/{dados[3].id}").status_code == 404
    assert logged.get("/hunter/empresas/99999").status_code == 404


# ---------- coleta ----------
def test_pagina_de_coleta_orienta_o_usuario(logged, db, world, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "cnpj_dir", str(tmp_path / "cnpj"))
    html = logged.get("/hunter/coleta").text
    assert "Base CNPJ" in html and "Google Places" in html and "Leitura de sites" in html
    assert "Você ainda não tem perfil" in html and "Nenhum arquivo em" in html
    db.add(IcpProfile(workspace_id=world.a.id, nome="Contadores MG", ufs=["MG"], termos_maps=["contador"]))
    db.commit()
    write_set(tmp_path / "cnpj")
    html = logged.get("/hunter/coleta").text
    assert "Contadores MG" in html and "Arquivos encontrados" in html and "estab (1)" in html


def test_estimativa_mostra_custo_e_exige_confirmacao(logged, db, world, filas, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "places_price_per_1000_usd", 10.0)
    monkeypatch.setattr(s, "google_places_api_key", "k")
    icp = IcpProfile(workspace_id=world.a.id, nome="Contadores", termos_maps=["contador"], ufs=["MG"],
                     municipios=["Patos de Minas", "Uberlândia"])
    db.add(icp)
    db.commit()
    tok = token(logged, "/hunter/coleta")
    r = logged.post("/hunter/coleta/places/estimar", data={"icp_id": icp.id, "csrf_token": tok})
    assert r.status_code == 200 and "Custo estimado" in r.text and "US$ 0.02" in r.text and "US$ 0.06" in r.text
    assert filas == []  # estimar nunca gasta nem enfileira
    r = logged.post("/hunter/coleta/places/rodar", data={"icp_id": icp.id, "csrf_token": tok})  # sem confirmar
    assert r.status_code == 303 and filas == []
    r = logged.post("/hunter/coleta/places/rodar", data={"icp_id": icp.id, "confirmo": "sim", "csrf_token": tok})
    assert r.status_code == 303 and filas == [("collect_places_job", (world.a.id, icp.id))]


def test_places_sem_chave_ou_perfil_incompleto(logged, db, world, filas, monkeypatch):
    monkeypatch.setattr(get_settings(), "google_places_api_key", "")
    icp = IcpProfile(workspace_id=world.a.id, nome="Sem termos", ufs=["MG"])
    ok = IcpProfile(workspace_id=world.a.id, nome="Ok", ufs=["MG"], termos_maps=["x"])
    db.add_all([icp, ok])
    db.commit()
    tok = token(logged, "/hunter/coleta")
    logged.post("/hunter/coleta/places/estimar", data={"icp_id": icp.id, "csrf_token": tok})
    assert "termos de busca" in logged.get("/hunter/coleta").text
    logged.post("/hunter/coleta/places/rodar", data={"icp_id": ok.id, "confirmo": "sim", "csrf_token": tok})
    assert filas == [] and "GOOGLE_PLACES_API_KEY" in logged.get("/hunter/coleta").text


def test_enfileira_cnpj_e_sites_e_limita_o_maximo(logged, db, world, filas):
    icp = IcpProfile(workspace_id=world.a.id, nome="P", ufs=["MG"], cnaes=["70.20-4"])
    db.add(icp)
    db.commit()
    tok = token(logged, "/hunter/coleta")
    logged.post("/hunter/coleta/cnpj", data={"icp_id": icp.id, "csrf_token": tok})
    logged.post("/hunter/coleta/sites", data={"icp_id": icp.id, "limite": "999999", "csrf_token": tok})
    logged.post("/hunter/coleta/sites", data={"icp_id": "", "limite": "-5", "csrf_token": tok})
    assert filas == [("collect_cnpj_job", (world.a.id, icp.id)), ("crawl_sites_job", (world.a.id, icp.id, 2000)),
                     ("crawl_sites_job", (world.a.id, None, 1))]


def test_fila_fora_do_ar_avisa(logged, db, world, monkeypatch):
    async def quebrada(*a, **k):
        raise ConnectionError("redis fora")

    monkeypatch.setattr(rotas, "enqueue", quebrada)
    tok = token(logged, "/hunter/coleta")
    logged.post("/hunter/coleta/sites", data={"icp_id": "", "limite": "10", "csrf_token": tok})
    assert "fila de tarefas está indisponível" in logged.get("/hunter/coleta").text


# ---------- progresso em tempo real ----------
def novo_job(db, ws, tipo="coleta_places", status="concluido", **cont):
    j = JobRun(workspace_id=ws.id, tipo=tipo, parametros_hash=f"h{tipo}{status}", status=status, contadores=cont,
               custo_usd=0.05)
    db.add(j)
    db.commit()
    return j


def test_lista_recente_e_detalhe_do_job(logged, db, world):
    j = novo_job(db, world.a, criadas=7)
    html = logged.get("/hunter/coleta").text
    assert "Google Places" in html and "7 empresas novas" in html and "US$ 0.050" in html
    assert "7 empresas novas" in logged.get("/hunter/coleta/recentes").text
    page = logged.get(f"/hunter/coleta/{j.id}").text
    assert "EventSource" in page and f"/hunter/coleta/{j.id}/eventos" in page


def test_eventos_sse_enviam_estado_e_encerram(logged, db, world):
    j = novo_job(db, world.a, criadas=7, consultas_feitas=3, avisos=["Limite de gasto atingido."])
    r = logged.get(f"/hunter/coleta/{j.id}/eventos")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    linhas = [l for l in r.text.splitlines() if l.startswith("data: ")]
    assert len(linhas) == 1  # job concluído: um evento e a conexão fecha
    d = json.loads(linhas[0][6:])
    assert d["status"] == "concluido" and d["custo_usd"] == 0.05 and d["contadores"]["criadas"] == 7


def test_job_de_outro_workspace_ou_tipo_e_404(logged, db, world):
    b = novo_job(db, world.b)
    outro_tipo = JobRun(workspace_id=world.a.id, tipo="scan_caixa", parametros_hash="x", status="concluido")
    db.add(outro_tipo)
    db.commit()
    for jid in (b.id, outro_tipo.id, 9999):
        assert logged.get(f"/hunter/coleta/{jid}").status_code == 404
        assert logged.get(f"/hunter/coleta/{jid}/eventos").status_code == 404


# ---------- permissões e isolamento ----------
def test_aprovador_ve_mas_nao_dispara_coletas(client, world, db, filas):
    icp = IcpProfile(workspace_id=world.a.id, nome="P", ufs=["MG"], termos_maps=["x"])
    db.add(icp)
    db.commit()
    login(client, "aprovador@teste.com")
    assert client.get("/hunter/coleta").status_code == 200 and client.get("/hunter/empresas").status_code == 200
    tok = token(client, "/hunter/coleta")
    for url, data in (("/hunter/coleta/cnpj", {"icp_id": icp.id}), ("/hunter/coleta/places/estimar", {"icp_id": icp.id}),
                      ("/hunter/coleta/places/rodar", {"icp_id": icp.id, "confirmo": "sim"}),
                      ("/hunter/coleta/sites", {"icp_id": icp.id, "limite": 5})):
        assert client.post(url, data={**data, "csrf_token": tok}).status_code == 403, url
    assert filas == []


def test_perfil_de_outro_workspace_e_recusado(logged, db, world, filas):
    alheio = IcpProfile(workspace_id=world.b.id, nome="Alheio", ufs=["RJ"], termos_maps=["x"])
    db.add(alheio)
    db.commit()
    tok = token(logged, "/hunter/coleta")
    for url in ("/hunter/coleta/cnpj", "/hunter/coleta/places/estimar", "/hunter/coleta/places/rodar",
                "/hunter/coleta/sites"):
        r = logged.post(url, data={"icp_id": alheio.id, "confirmo": "sim", "limite": 5, "csrf_token": tok})
        assert r.status_code == 404, url
    assert filas == []


def test_sem_login_redireciona(client, world):
    for p in ("/hunter/empresas", "/hunter/empresas/1", "/hunter/coleta", "/hunter/coleta/recentes", "/hunter/coleta/1",
              "/hunter/coleta/1/eventos"):
        assert client.get(p).headers["location"] == "/login", p


# ---------- funções do worker ----------
def test_jobs_do_worker(db, world, tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "cnpj_dir", str(tmp_path))
    write_set(tmp_path)
    icp = IcpProfile(workspace_id=world.a.id, nome="Consultoria SP", cnaes=["70.20-4"], ufs=["SP"], portes=["ME", "EPP"])
    db.add(icp)
    db.commit()
    r = asyncio.run(collect_cnpj_job({}, world.a.id, icp.id))
    assert r["criadas"] == 2 and not r["repetido"]
    assert asyncio.run(collect_cnpj_job({}, world.a.id, icp.id))["repetido"]
    assert asyncio.run(collect_cnpj_job({}, world.b.id, icp.id)) == {"erro": "perfil não existe"}  # perfil de outro workspace
    assert asyncio.run(collect_places_job({}, world.a.id, 9999)) == {"erro": "perfil não existe"}
    r = asyncio.run(crawl_sites_job({}, world.a.id, None, 5))
    assert r["sites"] == 0 and db.scalar(select(Company.id).where(Company.workspace_id == world.a.id)) is not None
