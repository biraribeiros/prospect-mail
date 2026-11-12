"""Etapa 2b: sinais comerciais (vagas, notícias, Maps). Nenhuma chamada real de rede."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select

from app.core.models import Company, IcpProfile, JobRun, OpportunitySignal
from app.hunter.companies.fetch import SafeFetcher
from app.hunter.signals.classify import classify_headline, mentions_company
from app.hunter.signals.service import run_signals, save_signal
from app.hunter.signals.sources import (CareersPageJobSource, GoogleNewsSource, RawSignal, RssJobSource, maps_signal)
from tests.conftest import login, token

NEWS = """<?xml version="1.0"?><rss><channel>
<item><title>Alfa Consultoria inaugura nova unidade em Campinas - Jornal X</title><link>https://jornal.example/a</link>
<pubDate>{hoje}</pubDate></item>
<item><title>Alfa Consultoria ganha prêmio de qualidade</title><link>https://jornal.example/b</link><pubDate>{hoje}</pubDate></item>
<item><title>Outra Empresa lança novo produto</title><link>https://jornal.example/c</link><pubDate>{hoje}</pubDate></item>
<item><title>Alfa Consultoria expande operação</title><link>https://jornal.example/old</link><pubDate>Mon, 01 Jan 2024 10:00:00 +0000</pubDate></item>
</channel></rss>"""

HOME = '<html><body><a href="/trabalhe-conosco">Trabalhe conosco</a></body></html>'
VAGAS = ("<html><body><h2>Analista de Marketing</h2><h2>Assistente Administrativo</h2><li>Nossa cultura</li>"
         "<li>Desenvolvedor Python</li></body></html>")


def hoje() -> str:
    return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")


def news_client(body: str | None = None, status=200):
    def handler(req: httpx.Request):
        return httpx.Response(status, text=(body or NEWS).replace("{hoje}", hoje()))
    return httpx.Client(transport=httpx.MockTransport(handler))


def fetcher_for(pages: dict[str, str]) -> SafeFetcher:
    def handler(req: httpx.Request):
        path = req.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        body = pages.get(path)
        return httpx.Response(200, text=body, headers={"content-type": "text/html"}) if body else httpx.Response(404)
    return SafeFetcher(http=httpx.Client(transport=httpx.MockTransport(handler)), url_check=lambda u: None,
                       sleep=lambda s: None, delay=0)


def make_company(db, ws, **kw):
    c = Company(workspace_id=ws.id, razao_social=kw.pop("razao_social", "ALFA CONSULTORIA LTDA"),
                nome_fantasia=kw.pop("nome_fantasia", "Alfa Consultoria"), site=kw.pop("site", "https://alfa.example"),
                dominio="alfa.example", uf="SP", **kw)
    db.add(c)
    db.commit()
    return c


# ---------- regras ----------
def test_classifica_manchetes():
    assert classify_headline("Empresa inaugura nova unidade em Campinas")[0] == "inauguracao"
    assert classify_headline("Grupo X anuncia expansão para o Sul")[0] == "expansao"
    assert classify_headline("Fulano assume como diretor de marketing")[0] == "diretoria"
    assert classify_headline("Empresa lança novo produto")[0] == "lancamento"
    assert classify_headline("Empresa demissões em massa")[1] == "negativo"
    assert classify_headline("Empresa ganha prêmio") is None


def test_exige_nome_da_empresa_na_manchete():
    assert mentions_company("Alfa Consultoria inaugura", ["Alfa Consultoria"])
    assert not mentions_company("Beta inaugura", ["Alfa Consultoria"])
    assert not mentions_company("Alf inaugura", ["Alf"])  # nome curto demais: ambíguo


# ---------- fontes ----------
def test_noticias_so_com_nome_tipo_e_data_recente():
    sig = GoogleNewsSource(news_client()).find("Alfa Consultoria", [])
    assert [s.tipo for s in sig] == ["inauguracao"]  # prêmio não é sinal; outra empresa não conta; notícia de 2024 é velha
    assert sig[0].url == "https://jornal.example/a" and sig[0].fonte == "google_news"
    assert sig[0].valido_ate > datetime.now(timezone.utc)


def test_feed_quebrado_nao_derruba():
    assert GoogleNewsSource(news_client("<<<não é xml", 200)).find("Alfa Consultoria", []) == []
    assert GoogleNewsSource(news_client("", 500)).find("Alfa Consultoria", []) == []


def test_feed_com_entidade_externa_e_ignorado():
    evil = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss><channel><item><title>&x;</title></item></channel></rss>'
    assert GoogleNewsSource(news_client(evil)).find("Alfa Consultoria", []) == []


def test_pagina_trabalhe_conosco_conta_cargos():
    f = fetcher_for({"/": HOME, "/trabalhe-conosco": VAGAS})
    sig = CareersPageJobSource(f).find("Alfa", "https://alfa.example")
    assert len(sig) == 1 and sig[0].tipo == "vaga" and "3 vaga(s)" in sig[0].titulo
    assert sig[0].url.endswith("/trabalhe-conosco")


def test_site_sem_pagina_de_vagas_nao_gera_sinal():
    assert CareersPageJobSource(fetcher_for({"/": "<html><body>oi</body></html>"})).find("Alfa", "https://alfa.example") == []
    assert CareersPageJobSource(fetcher_for({})).find("Alfa", None) == []


def test_rss_de_vagas_filtra_pelo_nome():
    body = ("<rss><channel><item><title>Alfa Consultoria contrata analista</title><link>https://v.example/1</link></item>"
            "<item><title>Beta contrata</title><link>https://v.example/2</link></item></channel></rss>")
    sig = RssJobSource(news_client(body), ["https://feed.example/?q={q}"]).find("Alfa Consultoria", None)
    assert [s.url for s in sig] == ["https://v.example/1"]


def test_maps_ativo_e_queda():
    ativo = maps_signal("Alfa", "https://maps.example/x", 4.6, 120, {"total": 100, "rating": 4.6})
    assert ativo.tipo == "maps_ativo" and ativo.polaridade == "positivo"
    queda = maps_signal("Alfa", "https://maps.example/x", 3.9, 102, {"total": 100, "rating": 4.5})
    assert queda.tipo == "maps_queda" and queda.polaridade == "negativo"
    assert maps_signal("Alfa", "https://maps.example/x", 4.5, 101, {"total": 100, "rating": 4.5}) is None
    assert maps_signal("Alfa", "", 4.5, 101, None) is None


# ---------- gravação ----------
def test_sem_fonte_verificavel_nao_grava(db, world):
    c = make_company(db, world.a)
    assert not save_signal(db, world.a.id, c, RawSignal("vaga", "x", "fonte", "javascript:alert(1)", 0.5))
    assert not save_signal(db, world.a.id, c, RawSignal("vaga", "x", "", "https://ok.example", 0.5))
    assert save_signal(db, world.a.id, c, RawSignal("vaga", "x", "fonte", "https://ok.example", 0.5))
    assert not save_signal(db, world.a.id, c, RawSignal("vaga", "x", "fonte", "https://ok.example", 0.5))  # repetido
    db.commit()
    assert db.scalar(select(func.count()).select_from(OpportunitySignal)) == 1


def test_run_signals_ponta_a_ponta_e_idempotente(db, world):
    c = make_company(db, world.a)
    other = make_company(db, world.b, razao_social="ZETA LTDA", nome_fantasia="Zeta Serviços")
    f = fetcher_for({"/": HOME, "/trabalhe-conosco": VAGAS})
    res = run_signals(db, world.a.id, None, fetcher=f, http=news_client())
    assert res["empresas"] == 1 and res["novos"] == 2 and res["vagas"] == 1 and res["noticias"] == 1
    tipos = set(db.scalars(select(OpportunitySignal.tipo).where(OpportunitySignal.company_id == c.id)))
    assert tipos == {"vaga", "inauguracao"}
    assert db.scalar(select(func.count()).select_from(OpportunitySignal).where(OpportunitySignal.company_id == other.id)) == 0
    assert db.scalar(select(JobRun.status).where(JobRun.tipo == "coleta_sinais")) == "concluido"
    again = run_signals(db, world.a.id, None, fetcher=f, http=news_client())
    assert again["repetido"] is True  # mesmo dia, mesmas empresas: não repete
    assert db.scalar(select(func.count()).select_from(OpportunitySignal)) == 2


def test_run_signals_perfil_filtra_empresas(db, world):
    make_company(db, world.a)
    icp = IcpProfile(workspace_id=world.a.id, nome="RJ", ufs=["RJ"])
    db.add(icp)
    db.commit()
    res = run_signals(db, world.a.id, icp, fetcher=fetcher_for({}), http=news_client())
    assert res["empresas"] == 0


def test_amostra_50_empresas_tem_ao_menos_um_sinal(db, world):
    for i in range(50):
        make_company(db, world.a, razao_social=f"EMPRESA {i} LTDA", nome_fantasia="Alfa Consultoria" if i == 7 else f"Empresa Numero {i}",
                     site=None)
    res = run_signals(db, world.a.id, None, limit=50, fetcher=fetcher_for({}), http=news_client())
    assert res["empresas"] == 50 and res["novos"] >= 1
    s = db.scalars(select(OpportunitySignal)).first()
    assert s.url.startswith("https://") and s.fonte


# ---------- telas ----------
def test_tela_de_sinais_e_pagina_da_empresa(logged, db, world):
    c = make_company(db, world.a)
    save_signal(db, world.a.id, c, RawSignal("expansao", "Alfa expande", "google_news", "https://jornal.example/e", 0.65,
                                             valido_ate=datetime.now(timezone.utc) + timedelta(days=10)))
    db.commit()
    html = logged.get("/hunter/sinais").text
    assert "Alfa expande" in html and 'href="https://jornal.example/e"' in html
    assert "Alfa expande" in logged.get(f"/hunter/empresas/{c.id}").text
    assert "Alfa expande" not in logged.get("/hunter/sinais?tipo=vaga").text


def test_sinais_isolados_por_workspace(client, db, world):
    other = make_company(db, world.b)
    save_signal(db, world.b.id, other, RawSignal("vaga", "Vaga secreta B", "x", "https://x.example/v", 0.5))
    db.commit()
    login(client, world.user_a.email)
    assert "Vaga secreta B" not in client.get("/hunter/sinais").text


def test_botao_de_buscar_sinais_existe_e_exige_csrf(logged):
    assert "/hunter/coleta/sinais" in logged.get("/hunter/coleta").text
    assert logged.post("/hunter/coleta/sinais", data={"limite": "10"}).status_code == 403
