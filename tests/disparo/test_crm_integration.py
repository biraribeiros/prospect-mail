"""Etapa 8: webhook do CRM — payload, assinatura, idempotência, tentativas e reenvio."""
import hashlib
import hmac
import json

import httpx
from sqlalchemy import func, select

from app.core.config import Settings
from app.core.models import Company, Contact, CrmEvent, Deal
from app.disparo import crm
from app.disparo.crm import resend_pending, send_event


def deal(db, ws):
    co = Company(workspace_id=ws.id, razao_social="ALFA LTDA", nome_fantasia="Alfa", cnpj="11222333000181", uf="SP", municipio="Campinas")
    db.add(co)
    db.flush()
    ct = Contact(workspace_id=ws.id, company_id=co.id, email="ana@alfa.com", nome_completo="Ana Lima", cargo="Diretora")
    db.add(ct)
    db.flush()
    d = Deal(workspace_id=ws.id, company_id=co.id, contact_id=ct.id, estagio="interessado")
    db.add(d)
    db.commit()
    return d


def cfg(monkeypatch, url="http://crm.test/hook", secret="s3cr3t"):
    monkeypatch.setattr(crm, "get_settings", lambda: Settings(crm_webhook_url=url, crm_webhook_secret=secret))


def client(handler):
    calls = []

    def wrap(req: httpx.Request):
        calls.append(req)
        return handler(req)
    return httpx.Client(transport=httpx.MockTransport(wrap)), calls


def test_payload_correto_e_assinado(db, world, monkeypatch):
    cfg(monkeypatch)
    d = deal(db, world.a)
    http, calls = client(lambda r: httpx.Response(201, json={"ok": True}))
    ev = send_event(db, world.a.id, d, "interessado", "Tenho interesse!", http)
    assert ev.status == "enviado" and ev.http_status == 201 and ev.tentativas == 1
    req = calls[0]
    body = json.loads(req.content)
    assert body["evento"] == "interessado" and body["empresa"]["cnpj"] == "11222333000181" and body["contato"]["email"] == "ana@alfa.com"
    assert body["resposta"] == "Tenho interesse!" and body["deal_id"] == d.id and body["origem"] == "prospector"
    assert req.headers["Idempotency-Key"] == f"{world.a.id}:{d.id}:interessado"
    assert req.headers["X-Prospector-Signature"] == hmac.new(b"s3cr3t", req.content, hashlib.sha256).hexdigest()


def test_nao_duplica_oportunidade(db, world, monkeypatch):
    cfg(monkeypatch)
    d = deal(db, world.a)
    http, calls = client(lambda r: httpx.Response(200))
    send_event(db, world.a.id, d, "interessado", "", http)
    send_event(db, world.a.id, d, "interessado", "", http)
    assert len(calls) == 1 and db.scalar(select(func.count()).select_from(CrmEvent)) == 1


def test_falha_5xx_tenta_ate_3_vezes_e_depois_reenvia(db, world, monkeypatch):
    cfg(monkeypatch)
    d = deal(db, world.a)
    http, calls = client(lambda r: httpx.Response(503))
    ev = send_event(db, world.a.id, d, "interessado", "", http)
    assert ev.status == "falhou" and ev.tentativas == 3 and len(calls) == 3
    ok, calls2 = client(lambda r: httpx.Response(200))
    assert resend_pending(db, world.a.id, ok) == 1
    assert db.scalar(select(CrmEvent.status)) == "enviado" and len(calls2) == 1


def test_erro_4xx_nao_repete(db, world, monkeypatch):
    cfg(monkeypatch)
    d = deal(db, world.a)
    http, calls = client(lambda r: httpx.Response(400))
    ev = send_event(db, world.a.id, d, "interessado", "", http)
    assert ev.status == "falhou" and len(calls) == 1


def test_erro_de_rede_vira_falha_sem_derrubar(db, world, monkeypatch):
    cfg(monkeypatch)
    d = deal(db, world.a)

    def boom(req):
        raise httpx.ConnectError("sem rede")
    http, calls = client(boom)
    assert send_event(db, world.a.id, d, "interessado", "", http).status == "falhou"


def test_sem_url_fica_sem_destino_e_envia_quando_configurar(db, world, monkeypatch):
    cfg(monkeypatch, url="")
    d = deal(db, world.a)
    assert send_event(db, world.a.id, d, "interessado").status == "sem_destino"
    cfg(monkeypatch)
    http, calls = client(lambda r: httpx.Response(200))
    assert resend_pending(db, world.a.id, http) == 1 and len(calls) == 1


def test_sem_segredo_nao_envia_assinatura(db, world, monkeypatch):
    cfg(monkeypatch, secret="")
    d = deal(db, world.a)
    http, calls = client(lambda r: httpx.Response(200))
    send_event(db, world.a.id, d, "interessado", "", http)
    assert "X-Prospector-Signature" not in calls[0].headers
