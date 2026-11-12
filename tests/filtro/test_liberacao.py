"""Regra do válido (quem já escreveu para você) e liberação manual de contatos no Filtro."""
from sqlalchemy import select

from app.core.models import Contact, Suppression, Verification
from app.filtro.validation.service import release_manually, run_validation
from tests.conftest import token
from tests.filtro.test_validation import FakeResolver, FakeVerifier


def contato(db, ws, email, origem="lista_texto", status="nao_verificado", recebidos=0, **kw):
    c = Contact(workspace_id=ws.id, email=email, origem=origem, email_status=status, qtd_recebidos=recebidos, **kw)
    db.add(c)
    db.flush()
    return c


def test_quem_ja_escreveu_para_voce_vira_valido_sem_verificador(db, world):
    ws = world.a
    amigo = contato(db, ws, "amigo@empresa.com.br", origem="caixa_email", recebidos=3)
    frio = contato(db, ws, "frio@empresa.com.br")
    contato(db, ws, "suprimido@bloq.com.br", recebidos=2)
    db.add(Suppression(workspace_id=ws.id, email_ou_dominio="bloq.com.br", motivo="descadastro", origem="manual"))
    db.commit()
    res = run_validation(db, ws.id, resolver=FakeResolver(), use_default_verifier=False)
    db.expire_all()
    assert res["verificados"] == 3
    assert db.get(Contact, amigo.id).email_status == "valido"
    v = db.scalar(select(Verification).where(Verification.contact_id == amigo.id))
    assert v.camada == "historico" and "já escreveu" in v.detalhes["motivo"]
    assert db.get(Contact, frio.id).email_status == "arriscado"  # sem verificador, o frio continua arriscado
    sup = db.scalar(select(Contact).where(Contact.email == "suprimido@bloq.com.br"))
    assert sup.email_status == "bloqueado"  # a supressão vence o histórico


def test_historico_nao_chama_o_verificador_pago(db, world):
    contato(db, world.a, "amigo@empresa.com.br", recebidos=1)
    db.commit()
    ver = FakeVerifier()
    run_validation(db, world.a.id, resolver=FakeResolver(), verifier=ver)
    assert ver.chamadas == []


def test_liberar_manualmente_barra_o_que_e_ruim_de_verdade(db, world):
    ws = world.a
    ok = contato(db, ws, "ana@boa.com.br")
    arriscado = contato(db, ws, "bia@boa.com.br", status="arriscado")
    sem_mx = contato(db, ws, "caio@semmx.com.br")
    inexistente = contato(db, ws, "dani@naoexiste.com.br")
    descartavel = contato(db, ws, "edu@mailinator.com")
    suprimido = contato(db, ws, "fabi@bloq.com.br")
    ja_valido = contato(db, ws, "gabi@boa.com.br", status="valido")
    bloqueado = contato(db, ws, "hugo@boa.com.br")
    bloqueado.bloqueado_envio = True
    outra_origem = contato(db, ws, "ivo@boa.com.br", origem="extensao_navegador")
    db.add(Suppression(workspace_id=ws.id, email_ou_dominio="bloq.com.br", motivo="descadastro", origem="manual"))
    db.commit()
    r = release_manually(db, ws.id, "a@teste.com", "lista_texto", resolver=FakeResolver(dominios_sem_mx={"semmx.com.br"},
                                                                                        inexistentes={"naoexiste.com.br"}))
    db.commit()
    db.expire_all()
    estados = {c.email: db.get(Contact, c.id).email_status for c in (ok, arriscado, sem_mx, inexistente, descartavel, suprimido,
                                                                    ja_valido, bloqueado, outra_origem)}
    assert estados["ana@boa.com.br"] == "valido" and estados["bia@boa.com.br"] == "valido"
    assert estados["caio@semmx.com.br"] == "invalido" and estados["dani@naoexiste.com.br"] == "invalido"
    assert estados["edu@mailinator.com"] == "bloqueado" and estados["fabi@bloq.com.br"] == "bloqueado"
    assert estados["gabi@boa.com.br"] == "valido"  # já era válido: não mexe
    assert estados["hugo@boa.com.br"] == "nao_verificado"  # bloqueado para envio: não mexe
    assert estados["ivo@boa.com.br"] == "nao_verificado"  # outra origem: não mexe
    assert r == {"liberados": 2, "reprovados": 4}
    v = db.scalar(select(Verification).where(Verification.contact_id == ok.id))
    assert v.camada == "manual" and "a@teste.com" in v.detalhes["motivo"] and v.provedor == "manual"


def test_liberar_manualmente_pela_tela(logged, db, world, monkeypatch):
    from app.filtro.validation import service

    c = contato(db, world.a, "ana@boa.com.br")
    db.commit()
    monkeypatch.setattr(service, "DnsResolver", lambda: FakeResolver())
    page = logged.get("/filtro").text
    assert "Liberar manualmente" in page and "Lista de texto (1)" in page
    r = logged.post("/filtro/liberar-manual", data={"origem": "lista_texto", "csrf_token": token(logged, "/filtro")})
    assert r.status_code == 303
    db.expire_all()
    assert db.get(Contact, c.id).email_status == "valido"


def test_aprovador_nao_libera_contatos(client, world):
    from tests.conftest import login

    login(client, world.approver_a.email)
    r = client.post("/filtro/liberar-manual", data={"origem": "", "csrf_token": token(client, "/filtro")})
    assert r.status_code == 403
