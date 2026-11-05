"""Google Places (simulado): máscara mínima, paginação, custo, cache de 30 dias, limite de gasto, evidência."""
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.models import ApiCache, Company, ComplianceRecord, Evidence, IcpProfile, JobRun
from app.hunter.companies import places as pl
from app.hunter.companies.service import run_places


@pytest.fixture(autouse=True)
def preco(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "places_price_per_1000_usd", 10.0)  # US$ 0,01 por chamada
    monkeypatch.setattr(s, "places_max_pages", 3)
    monkeypatch.setattr(s, "places_max_usd_per_run", 5.0)
    monkeypatch.setattr(s, "google_places_api_key", "chave-de-teste")


def perfil(db, ws, **kw):
    base = dict(nome="Contadores", termos_maps=["escritório de contabilidade"], municipios=["Patos de Minas"],
                ufs=["MG"])
    base.update(kw)
    i = IcpProfile(workspace_id=ws.id, **base)
    db.add(i)
    db.commit()
    return i


def place(pid, nome, site=None, status="OPERATIONAL", addr="Rua A, 10 - Centro, Patos de Minas - MG, 38700-000, Brasil",
          tel="(34) 3822-1000"):
    p = {"id": pid, "displayName": {"text": nome, "languageCode": "pt-BR"}, "formattedAddress": addr,
         "googleMapsUri": f"https://maps.google.com/?cid={pid}", "businessStatus": status,
         "nationalPhoneNumber": tel}
    if site:
        p["websiteUri"] = site
    return p


class Fake:
    """Servidor simulado: 1ª página com 2 lugares e token; 2ª página com 1 lugar."""

    def __init__(self, status=200):
        self.calls, self.status = [], status

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        self.calls.append((req, body))
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "x"})
        if body.get("pageToken") == "T2":
            return httpx.Response(200, json={"places": [place("p3", "Contabil Gama", "https://www.gama.com.br/")]})
        return httpx.Response(200, json={"places": [
            place("p1", "Contabil Alfa", "https://alfa.com.br/contato"),
            place("p2", "Contabil Beta Fechada", status="CLOSED_PERMANENTLY")], "nextPageToken": "T2"})


def client(fake):
    return pl.PlacesClient("chave-de-teste", httpx.Client(transport=httpx.MockTransport(fake)))


def test_consultas_do_perfil(db, world):
    assert pl.build_queries(perfil(db, world.a)) == ["escritório de contabilidade em Patos de Minas, MG"]
    assert pl.build_queries(perfil(db, world.a, municipios=[], ufs=["MG", "SP"], termos_maps=["a", "b"])) == [
        "a em MG", "a em SP", "b em MG", "b em SP"]
    with pytest.raises(pl.PlacesError, match="termos de busca"):
        pl.build_queries(perfil(db, world.a, termos_maps=[]))
    with pytest.raises(pl.PlacesError, match="cidade ou estado"):
        pl.build_queries(perfil(db, world.a, municipios=[], ufs=[]))


def test_endereco():
    assert pl.parse_address("Av. X, 5 - Bairro, São Paulo - SP, 01000-000, Brasil") == ("São Paulo", "SP")
    assert pl.parse_address("Rua Y, 1, Patos de Minas - MG, Brasil") == ("Patos de Minas", "MG")
    assert pl.parse_address("sem formato") == (None, None) and pl.parse_address(None) == (None, None)


def test_estimativa_antes_de_rodar(db, world):
    i = perfil(db, world.a, municipios=["Patos de Minas", "Uberlândia"])
    e = pl.estimate(db, world.a.id, i, get_settings())
    assert (e.consultas, e.em_cache, e.novas) == (2, 0, 2)
    assert e.custo_min == 0.02 and e.custo_max == 0.06  # 2 consultas × 1..3 páginas × US$ 0,01
    # uma consulta já em cache não entra na conta
    pl._store(db, world.a.id, pl.cache_key(pl.build_queries(i)[0], None), {"places": []}, 30)
    e2 = pl.estimate(db, world.a.id, i, get_settings())
    assert (e2.em_cache, e2.novas, e2.custo_min, e2.custo_max) == (1, 1, 0.01, 0.03)


def test_coleta_pagina_grava_empresas_com_evidencia(db, world):
    fake = Fake()
    res = pl.collect(db, world.a.id, perfil(db, world.a), client(fake), get_settings())
    db.commit()
    assert res["chamadas_pagas"] == 2 and res["criadas"] == 2 and res["ignoradas"] == 1  # a fechada é ignorada
    assert abs(res["custo_usd"] - 0.02) < 1e-9
    req, body = fake.calls[0]
    assert req.headers["x-goog-api-key"] == "chave-de-teste"
    mask = req.headers["x-goog-fieldmask"]
    assert "places.id" in mask and "nextPageToken" in mask and "reviews" not in mask  # máscara mínima
    assert body["textQuery"] == "escritório de contabilidade em Patos de Minas, MG" and body["regionCode"] == "BR"
    assert fake.calls[1][1]["pageToken"] == "T2"
    alfa = db.scalar(select(Company).where(Company.nome_fantasia == "Contabil Alfa"))
    assert (alfa.dominio, alfa.municipio, alfa.uf, alfa.telefone) == ("alfa.com.br", "Patos de Minas", "MG", "3438221000")
    ev = {e.campo: e for e in db.scalars(select(Evidence).where(Evidence.entidade_id == alfa.id))}
    assert {"google_place_id", "nome_fantasia", "site", "municipio", "uf", "telefone", "endereco"} <= set(ev)
    assert all(e.fonte == "google_places" and e.url == "https://maps.google.com/?cid=p1" for e in ev.values())
    assert db.scalar(select(func.count()).select_from(ComplianceRecord).where(
        ComplianceRecord.origem_dado == "google_places")) == 2
    assert db.scalar(select(func.count()).select_from(Company)) == 2


def test_segunda_coleta_usa_cache_e_nao_gasta(db, world):
    i = perfil(db, world.a)
    pl.collect(db, world.a.id, i, client(Fake()), get_settings())
    db.commit()
    fake2 = Fake()
    res = pl.collect(db, world.a.id, i, client(fake2), get_settings())
    db.commit()
    assert fake2.calls == [] and res["chamadas_pagas"] == 0 and res["chamadas_em_cache"] == 2 and res["custo_usd"] == 0
    assert res["criadas"] == 0 and res["atualizadas"] == 2
    assert db.scalar(select(func.count()).select_from(Company)) == 2  # sem duplicar (place_id)


def test_cache_expira_em_30_dias(db, world):
    i = perfil(db, world.a)
    pl.collect(db, world.a.id, i, client(Fake()), get_settings())
    db.commit()
    for row in db.scalars(select(ApiCache)):
        assert timedelta(days=29) < row.expira_em - datetime.now(timezone.utc) <= timedelta(days=30)
        row.expira_em = datetime.now(timezone.utc) - timedelta(seconds=1)  # vencido
    db.commit()
    fake = Fake()
    res = pl.collect(db, world.a.id, i, client(fake), get_settings())
    assert len(fake.calls) == 2 and res["chamadas_pagas"] == 2


def test_limite_de_gasto_para_sozinho(db, world, monkeypatch):
    monkeypatch.setattr(get_settings(), "places_max_usd_per_run", 0.015)  # cabe 1 chamada de US$ 0,01
    fake = Fake()
    res = pl.collect(db, world.a.id, perfil(db, world.a), client(fake), get_settings())
    assert len(fake.calls) == 1 and res["custo_usd"] == 0.01 and any("Limite de gasto" in a for a in res["avisos"])


def test_empresa_do_places_e_encontrada_pelo_dominio(db, world):
    db.add(Company(workspace_id=world.a.id, razao_social="Alfa Ltda", dominio="alfa.com.br"))
    db.commit()
    pl.collect(db, world.a.id, perfil(db, world.a), client(Fake()), get_settings())
    db.commit()
    alfa = db.scalar(select(Company).where(Company.dominio == "alfa.com.br"))
    assert alfa.razao_social == "Alfa Ltda" and alfa.nome_fantasia == "Contabil Alfa"  # completou, não duplicou
    assert db.scalar(select(func.count()).select_from(Company)) == 2


@pytest.mark.parametrize("status,msg", [(403, "recusou"), (429, "Limite de consultas")])
def test_erros_da_api(db, world, status, msg):
    with pytest.raises(pl.PlacesError, match=msg):
        pl.collect(db, world.a.id, perfil(db, world.a), client(Fake(status)), get_settings())


def test_sem_chave_nao_roda():
    with pytest.raises(pl.PlacesError, match="GOOGLE_PLACES_API_KEY"):
        pl.PlacesClient("")


def test_job_places_registra_custo_e_nao_repete_no_mesmo_dia(db, world):
    i = perfil(db, world.a)
    r = run_places(db, world.a.id, i, client(Fake()))
    job = db.get(JobRun, r["job_id"])
    db.refresh(job)
    assert job.status == "concluido" and float(job.custo_usd) == 0.02 and job.contadores["criadas"] == 2
    assert run_places(db, world.a.id, i, client(Fake()))["repetido"]
    # erro esperado (sem termos) vira job falho com mensagem, sem derrubar o worker
    r2 = run_places(db, world.a.id, perfil(db, world.a, termos_maps=[]), client(Fake()))
    assert "termos de busca" in r2["erro"]
