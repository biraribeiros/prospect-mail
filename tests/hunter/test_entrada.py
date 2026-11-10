"""Entrada simples de emails: lista em qualquer texto e API da extensão do navegador."""
import io
import re
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.limits import set_limits
from app.core.models import (ComplianceRecord, Company, Contact, Evidence, ExtensionToken, MailboxAccount,
                             WorkspaceMember)
from app.core.security import encrypt_json
from app.hunter import ingest
from app.main import app
from tests.conftest import login, token

TEXTO = """
Lista de leads (copiada do site):
Maria Souza <maria@empresa.com.br>; joao.silva@outraempresa.com.br,
contato@empresa.com.br  e  MARIA@empresa.com.br (repetido, outra caixa)
fale com pedro [at] terceira.com.br ou ana(at)quarta.com [dot] br
logo@2x.png  no-reply@empresa.com.br  conta@itau.com.br  isto-nao-e-email@  @sozinho.com
mailto:vendas@quinta.com.br?subject=oi
"""
ESPERADOS = {"maria@empresa.com.br", "joao.silva@outraempresa.com.br", "contato@empresa.com.br",
             "pedro@terceira.com.br", "ana@quarta.com.br", "vendas@quinta.com.br"}


# ---------- extração ----------
def test_extrai_emails_de_texto_solto_e_formas_disfarcadas():
    achados = set(ingest.extract_emails(*ingest.texts_of(TEXTO.encode())))
    assert ESPERADOS <= achados
    assert "logo@2x.png" not in {e for e in map(ingest._normalize, achados) if e}  # nome de imagem não é email


def test_extrai_de_docx_xlsx_e_doc_antigo_em_utf16():
    def pacote(nome_parte, xml):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("[Content_Types].xml", "<x/>")
            z.writestr(nome_parte, xml)
        return buf.getvalue()

    docx = pacote("word/document.xml", "<w:t>Fale com ana@docx.com.br</w:t>")
    xlsx = pacote("xl/sharedStrings.xml", "<si><t>bia@planilha.com.br</t></si>")
    doc_antigo = b"\xd0\xcf\x11\xe0" + "lixo carlos@doc.com.br fim".encode("utf-16-le")
    assert "ana@docx.com.br" in ingest.extract_emails(*ingest.texts_of(docx))
    assert "bia@planilha.com.br" in ingest.extract_emails(*ingest.texts_of(xlsx))
    assert "carlos@doc.com.br" in ingest.extract_emails(*ingest.texts_of(doc_antigo))


def test_pdf_e_arquivo_enorme_sao_recusados_com_mensagem_clara():
    with pytest.raises(ingest.IngestError, match="PDF"):
        ingest.texts_of(b"%PDF-1.7 ...")
    with pytest.raises(ingest.IngestError, match="10 MB"):
        ingest.texts_of(b"a" * (ingest.MAX_ARQUIVO + 1))


# ---------- importação para o banco ----------
def test_importa_lista_limpa_ruido_duplicados_e_guarda_a_origem(db, world):
    empresa = Company(workspace_id=world.a.id, razao_social="EMPRESA LTDA", dominio="empresa.com.br")
    db.add(empresa)
    db.add(Contact(workspace_id=world.a.id, email="joao.silva@outraempresa.com.br"))  # já existia
    db.add(MailboxAccount(workspace_id=world.a.id, tipo="imap", nome="minha", dominio="meudominio.com.br",
                          tokens_enc=encrypt_json({"emails": ["maria@empresa.com.br"]})))  # a própria caixa
    db.commit()
    res = ingest.import_text_list(db, world.a.id, TEXTO.encode(), "leads.txt")
    db.commit()
    assert res.novos == 4 and res.ja_existiam == 1 and res.ruido >= 3  # no-reply, itaú e a própria caixa
    emails = set(db.scalars(select(Contact.email).where(Contact.workspace_id == world.a.id)))
    assert "maria@empresa.com.br" not in emails and "conta@itau.com.br" not in emails
    c = db.scalar(select(Contact).where(Contact.email == "contato@empresa.com.br"))
    assert c.origem == "lista_texto" and c.email_status == "nao_verificado" and c.company_id == empresa.id
    ev = db.scalar(select(Evidence).where(Evidence.entidade_id == c.id, Evidence.campo == "email"))
    assert ev.fonte == "lista_texto" and ev.url == "lista://leads.txt"
    assert db.scalar(select(ComplianceRecord).where(ComplianceRecord.entidade_id == c.id)) is not None
    # a mesma lista outra vez não faz nada
    de_novo = ingest.import_text_list(db, world.a.id, TEXTO.encode(), "leads.txt")
    assert de_novo.arquivo_repetido and de_novo.novos == 0


def test_limite_de_contatos_do_workspace_vale_para_a_lista(db, world):
    set_limits(db, world.a.id, {"max_contatos": 2})
    db.commit()
    res = ingest.import_text_list(db, world.a.id, TEXTO.encode(), "leads.txt")
    db.commit()
    assert res.novos == 2 and res.limite
    assert db.query(Contact).filter_by(workspace_id=world.a.id).count() == 2


# ---------- telas ----------
def test_tela_de_lista_importa_texto_colado_e_mostra_os_contatos(logged):
    assert logged.get("/hunter/lista").status_code == 200
    r = logged.post("/hunter/lista", data={"texto": TEXTO, "csrf_token": token(logged, "/hunter/lista")})
    assert r.status_code == 200 and "contato(s) novo(s)" in r.text
    lista = logged.get("/hunter/contatos?origem=lista_texto").text
    assert "pedro@terceira.com.br" in lista
    assert "pedro@terceira.com.br" not in logged.get("/hunter/contatos").text  # a lista padrão são só as caixas


def test_tela_de_lista_aceita_arquivo_e_recusa_pdf_e_vazio(logged):
    tok = token(logged, "/hunter/lista")
    ok = logged.post("/hunter/lista", data={"csrf_token": tok}, files={"file": ("l.csv", b"nome,email\nA,a@x.com.br\n")})
    assert ok.status_code == 200 and "1 contato(s) novo(s)" in ok.text
    pdf = logged.post("/hunter/lista", data={"csrf_token": tok}, files={"file": ("l.pdf", b"%PDF-1.4 x")})
    assert pdf.status_code == 422 and "PDF" in pdf.text
    assert logged.post("/hunter/lista", data={"csrf_token": tok, "texto": "  "}).status_code == 422


def test_aprovador_nao_importa_lista(client, world):
    login(client, world.approver_a.email)
    r = client.post("/hunter/lista", data={"texto": "a@x.com.br", "csrf_token": token(client, "/hunter/lista")})
    assert r.status_code == 403


# ---------- chave e API da extensão ----------
def gerar_chave(logged) -> str:
    r = logged.post("/hunter/extensao/chave", data={"csrf_token": token(logged, "/hunter/extensao")})
    assert r.status_code == 200
    return re.search(r"pxt_[A-Za-z0-9_\-]+", r.text).group(0)


def api():
    return TestClient(app, follow_redirects=False)  # sem cookie nenhum: só a chave autentica


def auth(chave):
    return {"Authorization": f"Bearer {chave}"}


def test_chave_so_aparece_uma_vez_e_o_banco_guarda_so_o_hash(logged, db):
    chave = gerar_chave(logged)
    assert chave not in logged.get("/hunter/extensao").text
    row = db.scalar(select(ExtensionToken))
    assert row.token_hash != chave and chave not in row.token_hash and len(row.token_hash) == 64


def test_extensao_envia_lote_sem_cookie_e_contatos_entram_no_workspace_certo(logged, db, world):
    chave = gerar_chave(logged)
    c = api()
    assert c.get("/api/extensao/ping", headers=auth(chave)).json() == {"ok": True, "workspace": world.a.nome}
    r = c.post("/api/extensao/emails", headers=auth(chave), json={
        "fonte": "https://site.com.br/contato?busca=segredo#x",
        "emails": ["Compras@Site.com.br", "compras@site.com.br", "no-reply@site.com.br", "lixo", "novo@outro.com.br"]})
    assert r.status_code == 200
    assert r.json() == {"novos": 2, "ja_existiam": 0, "ruido": 1, "invalidos": 1, "limite": False}
    ct = db.scalar(select(Contact).where(Contact.email == "compras@site.com.br"))
    assert ct.workspace_id == world.a.id and ct.origem == "extensao_navegador"
    ev = db.scalar(select(Evidence).where(Evidence.entidade_id == ct.id, Evidence.campo == "email"))
    assert ev.url == "https://site.com.br/contato" and float(ev.confianca) == 0.4  # sem parâmetros da busca
    again = c.post("/api/extensao/emails", headers=auth(chave), json={"fonte": "", "emails": ["compras@site.com.br"]})
    assert again.json()["novos"] == 0 and again.json()["ja_existiam"] == 1
    assert db.scalar(select(ExtensionToken).where(ExtensionToken.ultimo_uso.is_not(None))) is not None


def test_api_recusa_sem_chave_chave_errada_e_chave_revogada(logged, db):
    chave = gerar_chave(logged)
    c = api()
    corpo = {"fonte": "", "emails": ["a@x.com.br"]}
    assert c.post("/api/extensao/emails", json=corpo).status_code == 401
    assert c.post("/api/extensao/emails", json=corpo, headers=auth("pxt_errada")).status_code == 401
    kid = db.scalar(select(ExtensionToken.id))
    logged.post(f"/hunter/extensao/{kid}/revogar", data={"csrf_token": token(logged, "/hunter/extensao")})
    assert c.post("/api/extensao/emails", json=corpo, headers=auth(chave)).status_code == 401
    assert db.query(Contact).count() == 0


def test_api_limita_o_tamanho_do_lote(logged):
    chave = gerar_chave(logged)
    r = api().post("/api/extensao/emails", headers=auth(chave), json={"fonte": "", "emails": ["a@x.com.br"] * 501})
    assert r.status_code == 422


def test_chave_de_quem_virou_aprovador_deixa_de_adicionar(logged, db, world):
    chave = gerar_chave(logged)
    m = db.scalar(select(WorkspaceMember).where(WorkspaceMember.user_id == world.user_a.id,
                                                WorkspaceMember.workspace_id == world.a.id))
    m.papel = "aprovador"
    db.commit()
    r = api().post("/api/extensao/emails", headers=auth(chave), json={"fonte": "", "emails": ["a@x.com.br"]})
    assert r.status_code == 403


def test_outro_workspace_nao_ve_nem_revoga_chave_alheia(logged, db, world):
    gerar_chave(logged)
    kid = db.scalar(select(ExtensionToken.id))
    outro = TestClient(app, follow_redirects=False)
    login(outro, world.user_b.email)
    assert outro.post(f"/hunter/extensao/{kid}/revogar",
                      data={"csrf_token": token(outro, "/hunter/extensao")}).status_code == 404
    assert "Nenhuma chave ainda." in outro.get("/hunter/extensao").text  # a lista do B não mostra a chave do A
    assert db.scalar(select(ExtensionToken.revogado)) is False


def test_baixar_extensao_gera_zip_com_manifesto(logged):
    r = logged.get("/hunter/extensao/baixar")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    nomes = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert {"prospector-extensao/manifest.json", "prospector-extensao/background.js",
            "prospector-extensao/popup.html"} <= set(nomes)
