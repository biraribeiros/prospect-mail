"""Rotas públicas (sem login): descadastro por link e One-Click (RFC 8058). O token é assinado; o POST não usa CSRF
porque quem chama é o provedor de email do destinatário."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from html import escape

from app.core.db import SessionLocal
from app.disparo.mime import read_unsub_token
from app.disparo.unsubscribe import suppress

router = APIRouter()

PAGE = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Descadastro</title></head><body style="font-family:system-ui;max-width:480px;margin:60px auto;padding:0 16px">{body}</body></html>"""


@router.get("/u/{token}", response_class=HTMLResponse)
def unsub_page(token: str):
    data = read_unsub_token(token)
    if not data:
        return HTMLResponse(PAGE.format(body="<h2>Link inválido</h2><p>Este link de descadastro não é válido.</p>"), status_code=404)
    body = (f"<h2>Deixar de receber mensagens</h2><p>Confirme para parar de receber emails enviados para "
            f"<b>{escape(data[1])}</b>.</p><form method='post'><button style='padding:10px 18px;font-size:16px'>Confirmar descadastro</button></form>")
    return HTMLResponse(PAGE.format(body=body))  # o GET não descadastra: robôs de antivírus abrem links


@router.post("/u/{token}", response_class=HTMLResponse)
async def unsub_do(token: str, request: Request):
    data = read_unsub_token(token)
    if not data:
        return HTMLResponse(PAGE.format(body="<h2>Link inválido</h2>"), status_code=404)
    ws, email = data
    with SessionLocal() as s:
        suppress(s, ws, email, origem="descadastro_link")
        s.commit()
    return HTMLResponse(PAGE.format(body="<h2>Pronto!</h2><p>Você não receberá mais mensagens nossas.</p>"))
