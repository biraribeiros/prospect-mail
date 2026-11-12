"""Etapa 4: validação de emails em camadas. Nenhuma chamada real (DNS e provedores simulados)."""
import threading
import time

import httpx
import pytest
from sqlalchemy import func, select

from app.core.models import ApiCache, Contact, JobRun, Suppression, Verification
from app.filtro.validation.layers import check_disposable, check_mx, check_role, check_syntax
from app.filtro.validation.service import catchall_cached, distribution, run_validation
from app.filtro.validation.verifiers import (MillionVerifier, ReacherVerifier, VerifierError, VerifierResult,
                                             ZeroBounceVerifier, build_verifier)
from tests.conftest import token


class FakeResolver:
    def __init__(self, dominios_sem_mx=(), inexistentes=()):
        self.sem_mx, self.inexistentes = set(dominios_sem_mx), set(inexistentes)
        self.consultas = []

    def mx(self, domain):
        self.consultas.append(domain)
        if domain in self.inexistentes:
            return None
        return [] if domain in self.sem_mx else [f"mx.{domain}"]


class FakeVerifier:
    nome = "fake"

    def __init__(self, respostas=None, padrao="valido", custo=0.001):
        self.respostas, self.padrao, self.custo = respostas or {}, padrao, custo
        self.chamadas = []
        self.simultaneas = 0
        self.max_simultaneas = 0
        self._lock = threading.Lock()

    def verify(self, email):
        with self._lock:
            self.simultaneas += 1
            self.max_simultaneas = max(self.max_simultaneas, self.simultaneas)
        time.sleep(0.01)
        self.chamadas.append(email)
        with self._lock:
            self.simultaneas -= 1
        r = self.respostas.get(email, self.padrao)
        if isinstance(r, Exception):
            raise r
        if r == "catch_all":
            return VerifierResult("arriscado", True, self.custo, "catch-all")
        return VerifierResult(r, None, self.custo, f"fake:{r}")


def contatos(db, ws, emails):
    for e in emails:
        db.add(Contact(workspace_id=ws.id, email=e))
    db.commit()


# ---------- camadas ----------
def test_sintaxe():
    assert check_syntax("Maria@Empresa.com.br")[0] == "maria@empresa.com.br"
    for ruim in ("sem-arroba", "a@", "@b.com", "a b@c.com", "a@@c.com", "a@c"):
        assert check_syntax(ruim)[1].status == "invalido"


def test_descartavel_e_conta_de_funcao():
    assert check_disposable("x@mailinator.com").status == "bloqueado"
    assert check_disposable("x@sub.yopmail.com").status == "bloqueado"
    assert check_disposable("x@empresa.com.br") is None
    assert check_role("postmaster@empresa.com.br").status == "arriscado"
    assert check_role("noreply@empresa.com.br").camada == "conta_funcao"
    assert check_role("maria@empresa.com.br") is None


def test_mx():
    r = FakeResolver(dominios_sem_mx={"semmx.com"}, inexistentes={"naoexiste.com"})
    assert check_mx("a@ok.com", r) is None
    assert check_mx("a@semmx.com", r).status == "invalido"
    assert "não existe" in check_mx("a@naoexiste.com", r).motivo


# ---------- provedores (HTTP simulado) ----------
def http_client(resposta, status=200):
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        return httpx.Response(status, json=resposta)
    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_reacher():
    http, seen = http_client({"is_reachable": "safe", "smtp": {"is_catch_all": False}, "misc": {}})
    r = ReacherVerifier("http://reacher:8080/", http).verify("a@b.com")
    assert r.status == "valido" and r.custo_usd == 0 and seen[0].url.path == "/v0/check_email"
    http, _ = http_client({"is_reachable": "risky", "smtp": {"is_catch_all": True}})
    assert ReacherVerifier("http://r", http).verify("a@b.com").catch_all is True
    http, _ = http_client({"is_reachable": "invalid"})
    assert ReacherVerifier("http://r", http).verify("a@b.com").status == "invalido"
    http, _ = http_client({}, 500)
    with pytest.raises(VerifierError):
        ReacherVerifier("http://r", http).verify("a@b.com")


@pytest.mark.parametrize("resp,status,catch", [({"result": "ok"}, "valido", None), ({"result": "invalid"}, "invalido", None),
                                               ({"result": "catch_all"}, "arriscado", True),
                                               ({"result": "unknown"}, "desconhecido", None)])
def test_millionverifier(resp, status, catch):
    http, seen = http_client(resp)
    r = MillionVerifier("CHAVE", http).verify("a@b.com")
    assert (r.status, r.catch_all) == (status, catch) and r.custo_usd > 0
    assert seen[0].url.params["email"] == "a@b.com"


def test_zerobounce_e_erros_do_provedor():
    http, _ = http_client({"status": "valid"})
    assert ZeroBounceVerifier("K", http).verify("a@b.com").status == "valido"
    http, _ = http_client({"status": "catch-all"})
    assert ZeroBounceVerifier("K", http).verify("a@b.com").catch_all is True
    http, _ = http_client({"status": "spamtrap"})
    assert ZeroBounceVerifier("K", http).verify("a@b.com").status == "invalido"
    http, _ = http_client({"error": "Invalid API key"})
    with pytest.raises(VerifierError):
        ZeroBounceVerifier("K", http).verify("a@b.com")
    http, _ = http_client({"error": "Insufficient credits"})
    with pytest.raises(VerifierError):
        MillionVerifier("K", http).verify("a@b.com")


def test_escolha_do_verificador_e_porta_25():
    v, aviso = build_verifier("nenhum")
    assert v is None and "Nenhum verificador" in aviso
    v, aviso = build_verifier("reacher", reacher_url="http://r:8080", port25=True)
    assert v.nome == "reacher" and aviso == ""
    v, aviso = build_verifier("reacher", api_key="K", reacher_url="http://r:8080", port25=False)
    assert v.nome == "millionverifier" and "Porta 25 fechada" in aviso
    v, aviso = build_verifier("reacher", reacher_url="http://r:8080", port25=False)
    assert v is None and "Porta 25 fechada" in aviso
    v, aviso = build_verifier("millionverifier")
    assert v is None and "VERIFIER_API_KEY" in aviso
    assert build_verifier("zerobounce", api_key="K")[0].nome == "zerobounce"


# ---------- fluxo completo ----------
def test_500_emails_distribuicao_coerente_e_sem_chamada_desnecessaria(db, world):
    ws = world.a
    emails = []
    emails += [f"pessoa{i}@bom.com.br" for i in range(300)]            # válidos
    emails += [f"x{i}@recusa.com.br" for i in range(60)]               # servidor recusa
    emails += [f"y{i}@tudoaceita.com.br" for i in range(40)]           # catch-all
    emails += [f"z{i}@semmx.com.br" for i in range(40)]                # sem servidor de email
    emails += [f"w{i}@mailinator.com" for i in range(30)]              # descartável
    emails += [f"s{i}-sem-arroba" for i in range(30)]                  # sintaxe
    assert len(emails) == 500
    contatos(db, ws, emails)
    ver = FakeVerifier({**{f"x{i}@recusa.com.br": "invalido" for i in range(60)},
                        **{f"y{i}@tudoaceita.com.br": "catch_all" for i in range(40)}})
    res = run_validation(db, ws.id, 1000, resolver=FakeResolver(dominios_sem_mx={"semmx.com.br"}), verifier=ver)
    assert res["verificados"] == 500 and res["falhas_provedor"] == 0
    d = distribution(db, ws.id)
    # catch-all: o primeiro de cada domínio vai ao provedor; os demais são resolvidos pelo cache do domínio na rodada seguinte
    assert d["valido"] == 300 and d["invalido"] == 60 + 40 + 30 and d["bloqueado"] == 30
    assert d["arriscado"] == 40 and d["nao_verificado"] == 0
    assert not any(e.endswith(("mailinator.com", "semmx.com.br")) or "sem-arroba" in e for e in ver.chamadas)  # camadas locais pararam antes
    assert ver.max_simultaneas <= 3
    assert res["custo_usd"] == pytest.approx(len(ver.chamadas) * 0.001)
    assert db.scalar(select(func.count()).select_from(Verification)) == 500


def test_dominio_catch_all_deixa_todos_arriscados_e_economiza_chamadas(db, world):
    ws = world.a
    contatos(db, ws, ["a@aceitatudo.com.br"])
    ver = FakeVerifier({"a@aceitatudo.com.br": "catch_all"})
    run_validation(db, ws.id, resolver=FakeResolver(), verifier=ver)
    assert catchall_cached(db, ws.id, "aceitatudo.com.br") is True
    contatos(db, ws, [f"p{i}@aceitatudo.com.br" for i in range(5)])
    ver2 = FakeVerifier()
    run_validation(db, ws.id, resolver=FakeResolver(), verifier=ver2)
    assert ver2.chamadas == []  # cache do domínio: nenhuma chamada paga
    assert set(db.scalars(select(Contact.email_status).where(Contact.email.like("%@aceitatudo.com.br")))) == {"arriscado"}


def test_cache_de_90_dias_e_reverificacao(db, world):
    ws = world.a
    contatos(db, ws, ["a@bom.com.br"])
    ver = FakeVerifier()
    run_validation(db, ws.id, resolver=FakeResolver(), verifier=ver)
    assert ver.chamadas == ["a@bom.com.br"]
    r2 = run_validation(db, ws.id, resolver=FakeResolver(), verifier=ver)
    assert r2["verificados"] == 0 and len(ver.chamadas) == 1  # dentro de 90 dias: nada a fazer
    from datetime import datetime, timedelta, timezone
    c = db.scalar(select(Contact))
    c.verificado_em = datetime.now(timezone.utc) - timedelta(days=91)
    db.commit()
    run_validation(db, ws.id, resolver=FakeResolver(), verifier=ver)
    assert len(ver.chamadas) == 2  # passou de 90 dias: verifica de novo
    run_validation(db, ws.id, force=True, resolver=FakeResolver(), verifier=ver)
    assert len(ver.chamadas) == 3


def test_expiracao_do_cache_de_catch_all(db, world):
    from datetime import datetime, timedelta, timezone
    from app.filtro.validation.service import catchall_store
    catchall_store(db, world.a.id, "x.com", True, 7)
    db.commit()
    assert catchall_cached(db, world.a.id, "x.com") is True
    row = db.scalar(select(ApiCache))
    row.expira_em = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    assert catchall_cached(db, world.a.id, "x.com") is None


def test_supressao_bloqueia_e_nao_chama_provedor(db, world):
    ws = world.a
    db.add_all([Suppression(workspace_id=ws.id, email_ou_dominio="a@bom.com.br", motivo="pediu", origem="manual"),
                Suppression(workspace_id=ws.id, email_ou_dominio="concorrente.com", motivo="x", origem="manual")])
    contatos(db, ws, ["a@bom.com.br", "b@concorrente.com", "c@bom.com.br"])
    ver = FakeVerifier()
    run_validation(db, ws.id, resolver=FakeResolver(), verifier=ver)
    assert ver.chamadas == ["c@bom.com.br"]
    assert distribution(db, ws.id)["bloqueado"] == 2


def test_sem_verificador_nunca_marca_valido(db, world):
    contatos(db, world.a, ["a@bom.com.br"])
    run_validation(db, world.a.id, resolver=FakeResolver(), verifier=None, use_default_verifier=False)
    assert db.scalar(select(Contact.email_status)) == "arriscado"
    assert "falta um verificador" in db.scalar(select(Verification.detalhes))["motivo"]


def test_falha_do_provedor_nao_marca_o_contato_e_tenta_de_novo(db, world):
    contatos(db, world.a, ["a@bom.com.br", "b@bom.com.br"])
    ver = FakeVerifier({"a@bom.com.br": VerifierError("sem saldo")})
    res = run_validation(db, world.a.id, resolver=FakeResolver(), verifier=ver)
    assert res["falhas_provedor"] == 1 and res["valido"] == 1
    assert db.scalar(select(Contact.email_status).where(Contact.email == "a@bom.com.br")) == "nao_verificado"
    ver2 = FakeVerifier()
    run_validation(db, world.a.id, resolver=FakeResolver(), verifier=ver2)
    assert ver2.chamadas == ["a@bom.com.br"]


def test_contato_bloqueado_para_envio_nao_gasta_verificacao(db, world):
    db.add(Contact(workspace_id=world.a.id, email="c@contador.com.br", bloqueado_envio=True, bloqueio_motivo="contador"))
    db.commit()
    ver = FakeVerifier()
    res = run_validation(db, world.a.id, resolver=FakeResolver(), verifier=ver)
    assert res["verificados"] == 0 and ver.chamadas == []


def test_tela_do_filtro(logged, db, world):
    contatos(db, world.a, ["a@bom.com.br"])
    db.add(Contact(workspace_id=world.b.id, email="segredo@outro.com"))
    db.commit()
    run_validation(db, world.a.id, resolver=FakeResolver(), verifier=FakeVerifier())
    html = logged.get("/filtro").text
    assert "não garante que a caixa existe" in html and "a@bom.com.br" in html and "segredo@outro.com" not in html
    assert "a@bom.com.br" in logged.get("/filtro?status=valido").text
    assert "a@bom.com.br" not in logged.get("/filtro?status=invalido").text
    assert logged.post("/filtro/validar", data={"limite": "10"}).status_code == 403  # sem CSRF
    r = logged.post("/filtro/validar", data={"limite": "10", "csrf_token": token(logged, "/filtro")})
    assert r.status_code == 303


def test_aprovador_nao_valida(client, db, world):
    from tests.conftest import login
    login(client, world.approver_a.email)
    r = client.post("/filtro/validar", data={"limite": "10", "csrf_token": token(client, "/filtro")})
    assert r.status_code == 403
