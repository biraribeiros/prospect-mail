"""Telas do Hunter: caixas, upload de .mbox, IMAP, contatos, filtro de ruído e isolamento entre workspaces."""
import asyncio

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.models import Contact, JobRun, MailboxAccount, MailInteraction
from app.core.security import decrypt_json
from app.web.routes import hunter as hunter_routes
from app.workers.jobs import scan_mailbox_job
from tests.conftest import login, token
from tests.hunter.helpers import OWN, SEGREDO_CORPO, build_1000, make_msg, write_mbox


@pytest.fixture(autouse=True)
def pasta_de_dados(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path / "data"))


@pytest.fixture
def filas(monkeypatch):
    """Substitui a fila: guarda os jobs pedidos em vez de falar com o Redis."""
    pedidos = []

    async def fake(job, *args, **kw):
        pedidos.append((job, args))

    monkeypatch.setattr(hunter_routes, "enqueue", fake)
    return pedidos


def upload(client, caminho, email=OWN, pasta="misto", nome="Minha caixa"):
    tok = token(client, "/hunter/caixas")
    with open(caminho, "rb") as f:
        return client.post("/hunter/caixas/mbox", data={"csrf_token": tok, "email": email, "pasta": pasta,
                                                       "nome": nome},
                           files={"file": ("caixa.mbox", f.read(), "application/mbox")})


def rodar_job(mailbox_id):
    return asyncio.run(scan_mailbox_job({}, mailbox_id))


def test_paginas_do_hunter_abrem(logged):
    for p in ("/hunter", "/hunter/caixas", "/hunter/contatos", "/hunter/ruido"):
        assert logged.get(p).status_code == 200, p
    assert "Em construção" in logged.get("/hunter").text  # empresas/sinais ainda não existem


def test_upload_mbox_cria_caixa_enfileira_e_varre(logged, world, db, filas, tmp_path):
    gab = build_1000(tmp_path / "origem.mbox")
    r = upload(logged, tmp_path / "origem.mbox")
    assert r.status_code == 303
    mb = db.scalar(select(MailboxAccount))
    assert mb.workspace_id == world.a.id and mb.tipo == "mbox" and filas == [("scan_mailbox_job", (mb.id,))]
    assert "caixa.mbox" not in (mb.tokens_enc or "")  # configuração cifrada
    res = rodar_job(mb.id)  # o que o worker faria
    assert res["contatos_novos"] == len(gab["externos"]) and not res["repetido"]
    assert rodar_job(mb.id)["repetido"]  # reprocessar o mesmo arquivo não faz nada
    db.expire_all()
    assert db.get(MailboxAccount, mb.id).saude == "ok"
    # tela de caixas mostra o resultado
    html = logged.get("/hunter/caixas").text
    assert "Minha caixa" in html and "novos" in html and OWN in html


def test_mesmo_arquivo_nao_pode_ser_enviado_duas_vezes(logged, db, filas, tmp_path):
    write_mbox(tmp_path / "a.mbox", [make_msg(mid="1", de="x@y.com.br", para=OWN, date=__import__("datetime").datetime.now(
        __import__("datetime").timezone.utc), assunto="a", corpo="b")])
    upload(logged, tmp_path / "a.mbox")
    r = upload(logged, tmp_path / "a.mbox", nome="Outra")
    assert r.status_code == 303
    assert db.scalar(select(func.count()).select_from(MailboxAccount)) == 1
    assert "já foi enviado" in logged.get("/hunter/caixas").text


def test_upload_exige_email_do_dono_e_valida_pasta(logged, db, filas, tmp_path):
    write_mbox(tmp_path / "a.mbox", [])
    upload(logged, tmp_path / "a.mbox", email="")
    assert db.scalar(select(func.count()).select_from(MailboxAccount)) == 0 and filas == []
    assert upload(logged, tmp_path / "a.mbox", pasta="invalida").status_code == 422


def test_upload_grande_demais_e_recusado(logged, db, filas, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "mbox_max_mb", 0)
    write_mbox(tmp_path / "a.mbox", [make_msg(mid="1", de="x@y.com.br", para=OWN,
                                              date=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
                                              assunto="a", corpo="b")])
    assert upload(logged, tmp_path / "a.mbox").status_code == 413
    assert db.scalar(select(func.count()).select_from(MailboxAccount)) == 0


def test_contatos_filtros_ordem_paginacao_e_csv(logged, world, db, filas, tmp_path):
    build_1000(tmp_path / "o.mbox")
    upload(logged, tmp_path / "o.mbox")
    rodar_job(db.scalar(select(MailboxAccount.id)))
    pag = logged.get("/hunter/contatos").text
    assert "Contatos encontrados" in pag and "Página 1 de" in pag and "Pessoa Numero" in pag
    scores = [int(x) for x in __import__("re").findall(r"<td><b>(\d+)</b></td>", pag)]
    assert scores == sorted(scores, reverse=True) and len(scores) == 50  # ordenado por nota, 50 por página
    assert "pessoa5@empresa5.com.br" in logged.get("/hunter/contatos?q=pessoa5@empresa5").text
    assert "Nenhum contato" in logged.get("/hunter/contatos?q=naoexiste").text
    assert "Nenhum contato" in logged.get("/hunter/contatos?tipo=pessoal").text  # todos são corporativos
    assert "Nenhum contato" in logged.get("/hunter/contatos?minimo=100").text
    assert logged.get("/hunter/contatos?q=%25").status_code == 200  # % não vira curinga
    csv = logged.get("/hunter/contatos.csv?minimo=0")
    assert csv.status_code == 200 and "attachment" in csv.headers["content-disposition"]
    linhas = csv.text.lstrip("﻿").strip().splitlines()
    assert linhas[0].startswith("nome_completo,email,cargo,telefone") and len(linhas) == 271  # 270 contatos
    assert SEGREDO_CORPO not in csv.text


def test_ruido_editavel_afeta_proxima_varredura(logged, world, db, filas, tmp_path):
    import datetime as dt

    agora = dt.datetime.now(dt.timezone.utc)
    write_mbox(tmp_path / "r.mbox", [
        make_msg(mid="1", de="cobranca-auto@fornecedor.com.br", para=OWN, date=agora, assunto="a", corpo="b"),
        make_msg(mid="2", de="gente@parceiro.com.br", para=OWN, date=agora, assunto="a", corpo="b")])
    tok = token(logged, "/hunter/ruido")
    r = logged.post("/hunter/ruido", data={"csrf_token": tok, "locais": "cobranca-auto", "dominios": "",
                                           "permitidos": ""})
    assert r.status_code == 303
    db.expire_all()
    assert db.get(type(world.a), world.a.id).config["ruido"]["locais"] == ["cobranca-auto"]
    assert "cobranca-auto" in logged.get("/hunter/ruido").text
    upload(logged, tmp_path / "r.mbox")
    rodar_job(db.scalar(select(MailboxAccount.id)))
    assert [c.email for c in db.scalars(select(Contact))] == ["gente@parceiro.com.br"]


# ---------- IMAP ----------
class ImapOk:
    def __init__(self, *a, **k):
        ImapOk.args = a

    def test_connection(self):
        pass


class ImapFalha(ImapOk):
    def test_connection(self):
        raise OSError("senha SENHA-SECRETA recusada pelo servidor")


def add_imap(client, **over):
    tok = token(client, "/hunter/caixas")
    data = {"csrf_token": tok, "nome": "Locaweb", "host": "imap.suaempresa.com.br", "port": "993",
            "user": "vendas@suaempresa.com.br", "password": "SENHA-SECRETA", "security": "ssl", "email": ""}
    data.update(over)
    return client.post("/hunter/caixas/imap", data=data)


def test_imap_testa_conexao_cifra_senha_e_enfileira(logged, db, filas, monkeypatch):
    monkeypatch.setattr(hunter_routes, "ImapAdapter", ImapOk)
    assert add_imap(logged).status_code == 303
    mb = db.scalar(select(MailboxAccount))
    assert mb.tipo == "imap" and mb.dominio == "suaempresa.com.br" and filas == [("scan_mailbox_job", (mb.id,))]
    assert "SENHA-SECRETA" not in mb.tokens_enc and "imap.suaempresa" not in mb.tokens_enc  # cifrado no banco
    cfg = decrypt_json(mb.tokens_enc)
    assert cfg["password"] == "SENHA-SECRETA" and cfg["emails"] == ["vendas@suaempresa.com.br"]
    assert ImapOk.args == ("imap.suaempresa.com.br", 993, "vendas@suaempresa.com.br", "SENHA-SECRETA", "ssl")
    pagina = logged.get("/hunter/caixas").text
    assert "SENHA-SECRETA" not in pagina


def test_imap_falha_nao_salva_e_nao_vaza_detalhes(logged, db, filas, monkeypatch):
    monkeypatch.setattr(hunter_routes, "ImapAdapter", ImapFalha)
    add_imap(logged)
    assert db.scalar(select(func.count()).select_from(MailboxAccount)) == 0 and filas == []
    pagina = logged.get("/hunter/caixas").text
    assert "Não consegui entrar" in pagina and "SENHA-SECRETA" not in pagina


@pytest.mark.parametrize("over,msg", [
    ({"host": "127.0.0.1"}, "rede interna"), ({"host": "meuservidor"}, "endereço completo"),
    ({"security": "plain"}, "conexão segura"), ({"port": "0"}, "Preencha"), ({"password": ""}, "Preencha"),
])
def test_imap_validacoes(logged, db, filas, monkeypatch, over, msg):
    monkeypatch.setattr(hunter_routes, "ImapAdapter", ImapOk)
    add_imap(logged, **over)
    assert db.scalar(select(func.count()).select_from(MailboxAccount)) == 0
    assert msg in logged.get("/hunter/caixas").text


# ---------- ações e permissões ----------
def test_varrer_e_remover_caixa(logged, world, db, filas, tmp_path):
    write_mbox(tmp_path / "a.mbox", [])
    upload(logged, tmp_path / "a.mbox")
    mb = db.scalar(select(MailboxAccount))
    caminho = decrypt_json(mb.tokens_enc)["path"]
    filas.clear()
    assert logged.post(f"/hunter/caixas/{mb.id}/varrer", data={"csrf_token": token(logged)}).status_code == 303
    assert filas == [("scan_mailbox_job", (mb.id,))]
    assert logged.post(f"/hunter/caixas/{mb.id}/excluir", data={"csrf_token": token(logged)}).status_code == 303
    assert db.scalar(select(func.count()).select_from(MailboxAccount)) == 0
    assert not __import__("os").path.exists(caminho)  # arquivo enviado também é apagado


def test_fila_fora_do_ar_mostra_aviso(logged, db, monkeypatch, tmp_path):
    async def quebrada(*a, **k):
        raise ConnectionError("redis fora")

    monkeypatch.setattr(hunter_routes, "enqueue", quebrada)
    write_mbox(tmp_path / "a.mbox", [])
    upload(logged, tmp_path / "a.mbox")
    assert "fila de tarefas está indisponível" in logged.get("/hunter/caixas").text
    assert db.scalar(select(func.count()).select_from(MailboxAccount)) == 1  # a caixa foi salva mesmo assim


def test_aprovador_so_visualiza(client, world, db, filas, tmp_path):
    login(client, "aprovador@teste.com")
    assert client.get("/hunter/caixas").status_code == 200
    assert client.get("/hunter/contatos").status_code == 200
    write_mbox(tmp_path / "a.mbox", [])
    assert upload(client, tmp_path / "a.mbox").status_code == 403
    tok = token(client, "/hunter/caixas")
    assert client.post("/hunter/ruido", data={"csrf_token": tok}).status_code == 403
    assert "Enviar arquivo" not in client.get("/hunter/caixas").text  # formulários nem aparecem


def test_sem_login_redireciona(client, world):
    for p in ("/hunter", "/hunter/caixas", "/hunter/contatos", "/hunter/contatos.csv", "/hunter/ruido"):
        assert client.get(p).headers["location"] == "/login", p


# ---------- isolamento entre workspaces ----------
def test_workspace_b_nao_ve_nem_aciona_caixa_do_a(client, world, db, filas, tmp_path):
    import datetime as dt

    write_mbox(tmp_path / "a.mbox", [make_msg(mid="1", de="cliente@segredo.com.br", para=OWN,
                                              date=dt.datetime.now(dt.timezone.utc), assunto="a", corpo="b")])
    login(client, world.user_a.email)
    upload(client, tmp_path / "a.mbox", nome="Caixa do A")
    mb_a = db.scalar(select(MailboxAccount))
    rodar_job(mb_a.id)
    client.post("/logout", data={"csrf_token": token(client)})

    login(client, world.user_b.email)  # usuário do workspace B
    assert "Caixa do A" not in client.get("/hunter/caixas").text
    assert "segredo.com.br" not in client.get("/hunter/contatos").text
    assert "segredo.com.br" not in client.get("/hunter/contatos.csv").text
    assert "segredo.com.br" not in client.get(f"/hunter/contatos?caixa={mb_a.id}").text
    tok = token(client, "/hunter/caixas")
    filas.clear()
    assert client.post(f"/hunter/caixas/{mb_a.id}/varrer", data={"csrf_token": tok}).status_code == 404
    assert client.post(f"/hunter/caixas/{mb_a.id}/excluir", data={"csrf_token": tok}).status_code == 404
    assert filas == [] and db.get(MailboxAccount, mb_a.id) is not None
    assert db.scalar(select(func.count()).select_from(MailInteraction)) == 1


def test_job_com_caixa_inexistente(db):
    assert rodar_job(999999) == {"erro": "caixa não existe"}


def test_job_com_falha_marca_caixa_com_erro(db, world, mailbox, tmp_path):
    mb = mailbox(tmp_path / "nao_existe.mbox")  # fora da pasta permitida -> falha ao montar o leitor
    with pytest.raises(ValueError):
        rodar_job(mb.id)
    db.expire_all()
    assert db.get(MailboxAccount, mb.id).saude == "erro"
    assert db.scalar(select(func.count()).select_from(JobRun).where(JobRun.tipo == "scan_caixa")) == 0
