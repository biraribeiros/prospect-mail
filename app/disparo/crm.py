"""Etapa 8: envio de oportunidades ao CRM externo por webhook — idempotente (nunca duplica) e com log de cada tentativa.

O CRM ainda não foi escolhido: sem CRM_WEBHOOK_URL os eventos ficam registrados como 'sem_destino' e podem ser reenviados depois.
O corpo é assinado com HMAC-SHA256 (cabeçalho X-Prospector-Signature) para o CRM conferir a origem.
"""
import hashlib
import hmac
import json
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.models import Company, Contact, CrmEvent, Deal

MAX_TENTATIVAS = 3


def build_payload(session: Session, deal: Deal, evento: str, trecho: str = "") -> dict:
    ct, co = session.get(Contact, deal.contact_id), (session.get(Company, deal.company_id) if deal.company_id else None)
    return {
        "evento": evento, "deal_id": deal.id, "estagio": deal.estagio, "ocorrido_em": datetime.now(timezone.utc).isoformat(),
        "empresa": {"nome": (co.nome_fantasia or co.razao_social) if co else None, "cnpj": co.cnpj if co else None,
                    "site": co.site if co else None, "municipio": co.municipio if co else None, "uf": co.uf if co else None},
        "contato": {"nome": ct.nome_completo, "cargo": ct.cargo, "email": ct.email, "telefone": ct.telefone},
        "resposta": trecho[:500], "origem": "prospector",
    }


def sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def send_event(session: Session, ws: int, deal: Deal, evento: str, trecho: str = "", http: httpx.Client | None = None) -> CrmEvent:
    """Registra e envia. A chave (workspace, deal, evento) garante que o mesmo evento não gera duas oportunidades no CRM."""
    s = get_settings()
    chave = f"{ws}:{deal.id}:{evento}"
    ev = session.scalar(select(CrmEvent).where(CrmEvent.chave == chave))
    if ev is not None and ev.status == "enviado":
        return ev
    if ev is None:
        ev = CrmEvent(workspace_id=ws, deal_id=deal.id, chave=chave, payload=build_payload(session, deal, evento, trecho))
        session.add(ev)
        session.flush()
    if not s.crm_webhook_url:
        ev.status = "sem_destino"
        return ev
    body = json.dumps(ev.payload, sort_keys=True, ensure_ascii=False).encode()
    headers = {"Content-Type": "application/json", "Idempotency-Key": chave}
    if s.crm_webhook_secret:
        headers["X-Prospector-Signature"] = sign(body, s.crm_webhook_secret)
    client = http or httpx.Client(timeout=15)
    for _ in range(MAX_TENTATIVAS - ev.tentativas):
        ev.tentativas += 1
        try:
            r = client.post(s.crm_webhook_url, content=body, headers=headers)
            ev.http_status = r.status_code
            if 200 <= r.status_code < 300:
                ev.status = "enviado"
                return ev
            if 400 <= r.status_code < 500 and r.status_code != 429:
                break  # erro do nosso lado (payload/URL): repetir não adianta
        except httpx.HTTPError:
            ev.http_status = None
    ev.status = "falhou"
    return ev


def resend_pending(session: Session, ws: int, http: httpx.Client | None = None) -> int:
    n = 0
    for ev in session.scalars(select(CrmEvent).where(CrmEvent.workspace_id == ws, CrmEvent.status.in_(("sem_destino", "falhou")))):
        ev.tentativas = 0 if ev.status == "falhou" else ev.tentativas
        deal = session.get(Deal, ev.deal_id) if ev.deal_id else None
        if deal is None:
            continue
        send_event(session, ws, deal, ev.chave.split(":")[-1], http=http)
        n += int(ev.status == "enviado")
    return n
