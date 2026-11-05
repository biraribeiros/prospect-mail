"""Crawler de sites (simulado): robots.txt, ritmo, proteção SSRF, extração de contatos e gravação com evidência."""
import httpx
import pytest
from sqlalchemy import func, select

from app.core.models import Company, ComplianceRecord, Contact, Evidence, IcpProfile, JobRun
from app.core.netsafe import check_url
from app.hunter.companies.crawler import crawl_site, normalize_site
from app.hunter.companies.extract import decode_cfemail, extract, valid_email
from app.hunter.companies.fetch import SafeFetcher
from app.hunter.companies.service import apply_crawl, run_crawl
from app.hunter.mailbox.noise import NoiseFilter
from tests.hunter.receita import cnpj_de


def cf_hex(addr: str, key: int = 0x5A) -> str:
    return f"{key:02x}" + "".join(f"{b ^ key:02x}" for b in addr.encode())


def site(mapa: dict[str, tuple[int, str] | str], robots: str | None = None, log: list | None = None):
    """Servidor simulado. mapa: caminho -> html ou (status, html). Demais caminhos: 404."""
    def handler(req: httpx.Request) -> httpx.Response:
        if log is not None:
            log.append(req.url.path)
        if req.url.path == "/robots.txt":
            return httpx.Response(200 if robots is not None else 404, text=robots or "")
        item = mapa.get(req.url.path)
        if item is None:
            return httpx.Response(404, text="nao achei")
        status, body = item if isinstance(item, tuple) else (200, item)
        return httpx.Response(status, text=body, headers={"content-type": "text/html; charset=utf-8"})
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def fetcher(http, **kw):
    kw.setdefault("url_check", lambda u: None)  # sem DNS nos testes
    kw.setdefault("sleep", lambda s: None)
    return SafeFetcher(http, user_agent="ProspectorBot/1.0", delay=1.0, **kw)


HOME = """<html><body><nav><a href="/contato">Fale conosco</a> <a href="/sobre-nos">Quem somos</a> <a href="https://outro.com/contato">x</a></nav>
<p>Bem-vindo à Alfa Contabilidade. Atendimento: (34) 3822-1000.</p>
<a href="https://wa.me/5534999991234">WhatsApp</a> <a href="https://www.instagram.com/alfacontabil/">insta</a></body></html>"""
CONTATO = f"""<html><body><h1>Contato</h1><p>Escreva para <a href="mailto:contato@alfa.com.br?subject=Oi">contato@alfa.com.br</a>
ou comercial [at] alfa [dot] com [dot] br. Sócio: socio&#64;alfa.com.br.</p>
<a class="__cf_email__" data-cfemail="{cf_hex('diretoria@alfa.com.br')}">[email protected]</a>
<p>CNPJ {cnpj_de('11111111')[:2]}.{cnpj_de('11111111')[2:5]}.{cnpj_de('11111111')[5:8]}/{cnpj_de('11111111')[8:12]}-{cnpj_de('11111111')[12:]}
 e 12.345.678/0001-00 (inválido). Img: logo@2x.png sentry@wixpress.com seu@email.com</p></body></html>"""


# ---------- extração ----------
def test_extrai_emails_de_varias_formas():
    info = extract(CONTATO, "https://alfa.com.br/contato")
    assert info.emails == {"contato@alfa.com.br", "comercial@alfa.com.br", "socio@alfa.com.br", "diretoria@alfa.com.br"}


def test_filtra_falsos_emails():
    assert valid_email("logo@2x.png") is None and valid_email("a@sentry.io") is None
    assert valid_email("seu@email.com") is None and valid_email("x@y") is None
    assert valid_email(" Contato@Alfa.com.br. ") == "contato@alfa.com.br"
    assert decode_cfemail(cf_hex("a@b.com")) == "a@b.com" and decode_cfemail("zz") is None


def test_extrai_telefone_whatsapp_cnpj_redes_e_links():
    info = extract(HOME + CONTATO, "https://alfa.com.br/")
    assert "3438221000" in info.phones and info.whatsapps == {"5534999991234"}
    assert info.cnpjs == {cnpj_de("11111111")}  # o inválido é descartado
    assert info.socials == {"instagram": "https://www.instagram.com/alfacontabil"}
    assert "https://alfa.com.br/contato" in info.links and "https://alfa.com.br/sobre-nos" in info.links
    assert not any("outro.com" in l for l in info.links)  # só links do mesmo site


def test_site_que_precisa_de_js():
    spa = '<html><body><div id="root"></div>' + "<script src='a.js'></script>" * 4 + "</body></html>"
    assert extract(spa, "https://x.com/").needs_js
    assert not extract(HOME, "https://x.com/").needs_js


# ---------- SSRF ----------
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost/admin", "http://10.0.0.5/", "http://192.168.0.1/", "http://172.16.0.1/",
    "http://169.254.169.254/latest/meta-data/", "http://[::1]/", "http://0.0.0.0/", "http://100.64.0.1/",
    "file:///etc/passwd", "ftp://x.com/", "http://x.com:8080/", "http://user:pw@8.8.8.8/", "http://servidor.internal/",
    "gopher://8.8.8.8/",
])
def test_ssrf_bloqueia_destinos_internos_e_protocolos(url):
    assert check_url(url)


def test_ssrf_permite_publico():
    assert check_url("https://8.8.8.8/") is None and check_url("http://1.1.1.1:80/") is None
    assert check_url("https://dominio-que-nao-existe.invalid/") == "domínio não encontrado"


def test_redirecionamento_para_rede_interna_e_bloqueado():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    f = SafeFetcher(httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None,
                    url_check=lambda u: check_url(u) if "169.254" in u else None)
    r = f.get("https://publico.example/")
    assert r.error == "bloqueado_rede" and r.text == ""


def test_redirecionamentos_demais():
    f = fetcher(httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(404) if req.url.path == "/robots.txt"
        else httpx.Response(302, headers={"location": "/b"}))))
    assert f.get("https://x.com/a").error == "redirecionamentos_demais"


# ---------- educação: robots, ritmo, limites ----------
def test_respeita_robots_txt():
    log = []
    f = fetcher(site({"/": HOME, "/contato": CONTATO, "/privado/x": "segredo"},
                     robots="User-agent: *\nDisallow: /privado\n", log=log))
    assert f.get("https://alfa.com.br/privado/x").error == "bloqueado_robots"
    assert "/privado/x" not in log and f.get("https://alfa.com.br/contato").ok


def test_robots_ausente_libera_e_robots_com_erro_5xx_bloqueia():
    assert fetcher(site({"/": HOME}, robots=None)).get("https://a.com/").ok
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    assert fetcher(http).get("https://a.com/").error == "bloqueado_robots"


def test_uma_requisicao_por_segundo_por_dominio():
    relogio, esperas = {"t": 100.0}, []

    def dorme(s):
        esperas.append(round(s, 3))
        relogio["t"] += s

    f = SafeFetcher(site({"/": HOME, "/contato": CONTATO}), delay=1.0, url_check=lambda u: None,
                    sleep=dorme, clock=lambda: relogio["t"])
    f.get("https://alfa.com.br/")
    f.get("https://alfa.com.br/contato")
    assert esperas and all(0 < e <= 1.0 for e in esperas)  # robots.txt, home e contato: sempre com intervalo
    antes = len(esperas)
    f.get("https://beta.com.br/")  # outro domínio: robots.txt do domínio novo não espera pelo anterior
    assert esperas[antes:] == [] or all(e <= 1.0 for e in esperas[antes:])


def test_crawl_delay_do_robots_vale_se_maior():
    esperas = []
    f = SafeFetcher(site({"/": HOME, "/a": HOME}, robots="User-agent: *\nCrawl-delay: 5\n"), delay=1.0,
                    url_check=lambda u: None, sleep=esperas.append, clock=lambda: 0.0)
    f.get("https://x.com/")
    f.get("https://x.com/a")
    assert max(esperas) == 5.0


def test_tipo_tamanho_e_timeout():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.path == "/doc.pdf":
            return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
        if req.url.path == "/lento":
            raise httpx.ReadTimeout("devagar")
        return httpx.Response(200, content=b"a" * 3_000_000, headers={"content-type": "text/html"})

    f = fetcher(httpx.Client(transport=httpx.MockTransport(handler)))
    assert f.get("https://x.com/doc.pdf").error == "tipo_nao_suportado"
    assert f.get("https://x.com/lento").error == "timeout"
    assert len(f.get("https://x.com/grande").text) == 2_000_000  # corta no limite


# ---------- leitura de um site inteiro ----------
def test_crawl_site_le_home_e_paginas_de_contato():
    log = []
    r = crawl_site(fetcher(site({"/": HOME, "/contato": CONTATO, "/sobre-nos": "<p>Somos a Alfa</p>"}, log=log)),
                   "alfa.com.br")
    assert r.status == "ok" and r.paginas_lidas == 3
    assert set(r.emails) == {"contato@alfa.com.br", "comercial@alfa.com.br", "socio@alfa.com.br", "diretoria@alfa.com.br"}
    assert r.emails["contato@alfa.com.br"] == "https://alfa.com.br/contato"  # guarda a página de origem
    assert r.whatsapps == {"5534999991234": "https://alfa.com.br/"} and r.cnpjs == {cnpj_de("11111111"): "https://alfa.com.br/contato"}
    assert r.redes["instagram"].endswith("alfacontabil")


def test_crawl_site_status():
    assert crawl_site(fetcher(site({"/": (500, "erro")})), "x.com").status == "inalcancavel"
    assert crawl_site(fetcher(site({"/": HOME}, robots="User-agent: *\nDisallow: /\n")), "x.com").status == "bloqueado_robots"
    assert crawl_site(fetcher(site({"/": HOME}), url_check=lambda u: "endereço de rede interna"), "x.com").status == "bloqueado_rede"
    spa = '<html><body><div id="root"></div>' + "<script src='a.js'></script>" * 4 + "</body></html>"
    assert crawl_site(fetcher(site({"/": spa})), "x.com").status == "precisa_js"
    assert crawl_site(fetcher(site({})), "").status == "erro"
    assert normalize_site("Alfa.com.br/pagina?x=1") == "https://Alfa.com.br/" and normalize_site("") is None


def test_limite_de_paginas():
    log = []
    crawl_site(fetcher(site({"/": HOME, "/contato": CONTATO, "/sobre": "x", "/equipe": "x", "/fale-conosco": "x",
                            "/quem-somos": "x"}, log=log)), "alfa.com.br", max_pages=2)
    assert [p for p in log if p != "/robots.txt"].count("/contato") <= 1 and len([p for p in log if p != "/robots.txt"]) <= 3


# ---------- gravação ----------
def make_company(db, ws, **kw):
    c = Company(workspace_id=ws.id, nome_fantasia="Alfa", site="https://alfa.com.br", dominio="alfa.com.br", **kw)
    db.add(c)
    db.commit()
    return c


def crawl_of(emails=None, **kw):
    from app.hunter.companies.crawler import CrawlResult

    return CrawlResult(status="ok", emails=emails or {}, **kw)


def test_apply_crawl_grava_contatos_com_evidencia_e_conformidade(db, world):
    c = make_company(db, world.a)
    res = crawl_of({"contato@alfa.com.br": "https://alfa.com.br/contato", "dono@gmail.com": "https://alfa.com.br/",
                    "noreply@alfa.com.br": "https://alfa.com.br/", "contador@outra.com.br": "https://alfa.com.br/"},
                   telefones={"3438221000": "https://alfa.com.br/"}, whatsapps={"5534999991234": "https://alfa.com.br/"},
                   cnpjs={cnpj_de("11111111"): "https://alfa.com.br/contato"}, redes={"instagram": "https://www.instagram.com/alfa"})
    got = apply_crawl(db, world.a.id, c, res, NoiseFilter())
    db.commit()
    assert got == {"emails": 3, "contatos_novos": 3}  # noreply é ruído
    assert (c.site_status, c.telefone, c.cnpj) == ("ok", "3438221000", cnpj_de("11111111")) and c.site_lido_em
    conf = {e.valor: float(e.confianca) for e in db.scalars(select(Evidence).where(Evidence.campo == "email"))}
    assert conf == {"contato@alfa.com.br": 0.8, "dono@gmail.com": 0.4, "contador@outra.com.br": 0.4}  # outro domínio = menos confiança
    contatos = {x.email: x for x in db.scalars(select(Contact))}
    assert contatos["contato@alfa.com.br"].company_id == c.id and contatos["contato@alfa.com.br"].origem == "site:alfa.com.br"
    assert contatos["dono@gmail.com"].dominio_tipo == "pessoal"
    assert db.scalar(select(func.count()).select_from(ComplianceRecord).where(
        ComplianceRecord.origem_dado == "crawler_site", ComplianceRecord.entidade_tipo == "contact")) == 3
    campos = {e.campo for e in db.scalars(select(Evidence).where(Evidence.entidade_tipo == "company"))}
    assert {"telefone", "whatsapp", "instagram", "cnpj"} <= campos
    assert all(e.fonte == "crawler_site" and e.url for e in db.scalars(select(Evidence)))


def test_apply_crawl_cnpj_so_se_for_unico_e_livre(db, world):
    c = make_company(db, world.a)
    apply_crawl(db, world.a.id, c, crawl_of(cnpjs={cnpj_de("11111111"): "u", cnpj_de("22222222"): "u"}), NoiseFilter())
    assert c.cnpj is None  # o rodapé citou dois CNPJs: não dá para saber qual é o da empresa
    outra = Company(workspace_id=world.a.id, cnpj=cnpj_de("33333333"), razao_social="Outra")
    db.add(outra)
    db.commit()
    apply_crawl(db, world.a.id, c, crawl_of(cnpjs={cnpj_de("33333333"): "u"}), NoiseFilter())
    assert c.cnpj is None  # já pertence a outra empresa (a etapa 3 resolve duplicidades)


def test_apply_crawl_contato_existente_so_ganha_empresa(db, world):
    c = make_company(db, world.a)
    db.add(Contact(workspace_id=world.a.id, email="contato@alfa.com.br", nome_completo="Ana"))
    db.commit()
    got = apply_crawl(db, world.a.id, c, crawl_of({"contato@alfa.com.br": "https://alfa.com.br/"}), NoiseFilter())
    ct = db.scalar(select(Contact))
    assert got["contatos_novos"] == 0 and ct.company_id == c.id and ct.nome_completo == "Ana"


# ---------- job ----------
def test_job_de_sites_seleciona_so_nao_lidos_respeita_perfil_e_nao_repete(db, world):
    a = make_company(db, world.a, uf="MG", municipio="Patos de Minas", cnae_principal="7020-4/00")
    b = Company(workspace_id=world.a.id, nome_fantasia="Beta", site="https://beta.com.br", dominio="beta.com.br", uf="SP")
    ja_lida = Company(workspace_id=world.a.id, nome_fantasia="Gama", site="https://gama.com.br", site_status="ok")
    sem_site = Company(workspace_id=world.a.id, nome_fantasia="Delta")
    outro_ws = Company(workspace_id=world.b.id, nome_fantasia="Eps", site="https://eps.com.br")
    db.add_all([b, ja_lida, sem_site, outro_ws])
    db.commit()
    perfil = IcpProfile(workspace_id=world.a.id, nome="MG", ufs=["MG"], cnaes=["70.20-4"])
    db.add(perfil)
    db.commit()
    http = site({"/": HOME, "/contato": CONTATO})
    r = run_crawl(db, world.a.id, perfil, 100, fetcher(http))
    db.refresh(a)
    assert r["sites"] == 1 and r["ok"] == 1 and r["emails"] == 4 and a.site_status == "ok"
    job = db.get(JobRun, r["job_id"])
    db.refresh(job)
    assert job.tipo == "coleta_sites" and job.status == "concluido" and job.contadores["contatos_novos"] == 4
    assert run_crawl(db, world.a.id, perfil, 100, fetcher(http))["sites"] == 0  # nada novo para ler
    r2 = run_crawl(db, world.a.id, None, 100, fetcher(http))  # sem perfil: pega o Beta (o do outro workspace não)
    assert r2["sites"] == 1 and db.scalar(select(Company.site_status).where(Company.id == b.id)) == "ok"
    assert db.scalar(select(Company.site_status).where(Company.id == outro_ws.id)) == "nao_lido"


def test_um_site_quebrado_nao_derruba_a_coleta(db, world):
    make_company(db, world.a)

    class Quebrado(SafeFetcher):
        def get(self, url):
            raise RuntimeError("bug")

    r = run_crawl(db, world.a.id, None, 10, Quebrado(site({}), url_check=lambda u: None))
    assert r["sites"] == 1 and r["bloqueados"] == 1
