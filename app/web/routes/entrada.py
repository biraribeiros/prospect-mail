"""Entrada simples de emails: importar lista (qualquer texto), chave da extensão e API que a extensão usa."""
import hashlib
import io
import secrets
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.models import AppUser, ExtensionToken, Workspace, WorkspaceMember
from app.hunter import ingest
from app.web.deps import Ctx, current_ctx, editor_ctx, flash, render

router = APIRouter()
EXT_DIR = Path(__file__).resolve().parents[2] / "extensao"
MAX_CHAVES = 5
PREFIXO = "pxt_"


# ---------- importar lista (qualquer texto) ----------
@router.get("/hunter/lista")
def lista_page(request: Request, ctx: Ctx = Depends(current_ctx)):
    return render(request, "hunter_lista.html", ctx, ativo="hunter")


@router.post("/hunter/lista")
async def lista_importar(request: Request, texto: str = Form(""), file: UploadFile | None = File(None),
                         ctx: Ctx = Depends(editor_ctx)):
    data, nome = b"", "texto-colado"
    if file is not None and file.filename:
        data, nome = await file.read(ingest.MAX_ARQUIVO + 1), file.filename
    elif texto.strip():
        data = texto.encode("utf-8")
    if not data:
        return render(request, "hunter_lista.html", ctx, status=422, ativo="hunter",
                      error="Cole um texto ou escolha um arquivo.")
    try:
        res = ingest.import_text_list(ctx.db, ctx.workspace_id, data, nome)
        ctx.db.commit()
    except ingest.IngestError as e:
        ctx.db.rollback()
        return render(request, "hunter_lista.html", ctx, status=422, ativo="hunter", error=str(e))
    return render(request, "hunter_lista.html", ctx, ativo="hunter", res=res, nome=nome)


# ---------- chave e download da extensão ----------
def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _chaves(ctx: Ctx) -> list[ExtensionToken]:
    return list(ctx.db.scalars(select(ExtensionToken).where(
        ExtensionToken.workspace_id == ctx.workspace_id, ExtensionToken.revogado.is_(False))
        .order_by(ExtensionToken.id)))


@router.get("/hunter/extensao")
def extensao_page(request: Request, ctx: Ctx = Depends(current_ctx)):
    return render(request, "hunter_extensao.html", ctx, ativo="hunter", chaves=_chaves(ctx))


@router.post("/hunter/extensao/chave")
def extensao_nova_chave(request: Request, ctx: Ctx = Depends(editor_ctx)):
    if len(_chaves(ctx)) >= MAX_CHAVES:
        flash(request, f"Limite de {MAX_CHAVES} chaves. Revogue uma que não usa mais.", "err")
        return RedirectResponse("/hunter/extensao", status_code=303)
    token = PREFIXO + secrets.token_urlsafe(32)
    ctx.db.add(ExtensionToken(workspace_id=ctx.workspace_id, user_id=ctx.user.id, token_hash=_hash(token)))
    ctx.db.commit()
    # a chave só aparece agora: não vai para a sessão, para a URL nem para o banco em texto puro
    return render(request, "hunter_extensao.html", ctx, ativo="hunter", chaves=_chaves(ctx), nova_chave=token)


@router.post("/hunter/extensao/{chave_id}/revogar")
def extensao_revogar(chave_id: int, request: Request, ctx: Ctx = Depends(editor_ctx)):
    c = ctx.db.get(ExtensionToken, chave_id)
    if c is None or c.workspace_id != ctx.workspace_id:  # isolamento entre workspaces
        raise HTTPException(status_code=404)
    c.revogado = True
    ctx.db.commit()
    flash(request, "Chave revogada. A extensão que a usava para de enviar.")
    return RedirectResponse("/hunter/extensao", status_code=303)


@router.get("/hunter/extensao/baixar")
def extensao_baixar(ctx: Ctx = Depends(current_ctx)):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(EXT_DIR.rglob("*")):
            if p.is_file():
                z.write(p, f"prospector-extensao/{p.relative_to(EXT_DIR).as_posix()}")
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="prospector-extensao.zip"'})


# ---------- API usada pela extensão (autenticada só pela chave; sem cookie, por isso fora do CSRF) ----------
class Lote(BaseModel):
    fonte: str = Field("", max_length=500)
    emails: list[Annotated[str, StringConstraints(max_length=254)]] = Field(max_length=500)


def chave_da_extensao(request: Request, db: Session = Depends(get_db)) -> ExtensionToken:
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    row = db.scalar(select(ExtensionToken).where(ExtensionToken.token_hash == _hash(token),
                                                 ExtensionToken.revogado.is_(False))) if token else None
    if row is None:
        raise HTTPException(status_code=401, detail="Chave inválida ou revogada.")
    user = db.get(AppUser, row.user_id)
    member = db.scalar(select(WorkspaceMember).where(WorkspaceMember.user_id == row.user_id,
                                                     WorkspaceMember.workspace_id == row.workspace_id))
    if user is None or not user.ativo or member is None or member.papel not in ("admin", "operador"):
        raise HTTPException(status_code=403, detail="Esta chave não tem permissão para adicionar contatos.")
    row.ultimo_uso = datetime.now(timezone.utc)
    return row


def _fonte(raw: str) -> str:
    """Só endereço da página (sem parâmetros nem #): consultas de busca podem conter dados pessoais."""
    p = urlsplit(raw.strip())
    if p.scheme not in ("http", "https") or not p.netloc:
        return "extensao://sem-fonte"
    return f"{p.scheme}://{p.netloc}{p.path}"[:500]


@router.get("/api/extensao/ping")
def api_ping(chave: ExtensionToken = Depends(chave_da_extensao), db: Session = Depends(get_db)):
    db.commit()
    return {"ok": True, "workspace": db.get(Workspace, chave.workspace_id).nome}


@router.post("/api/extensao/emails")
def api_emails(lote: Lote, chave: ExtensionToken = Depends(chave_da_extensao), db: Session = Depends(get_db)):
    res = ingest.ingest_emails(db, chave.workspace_id, lote.emails, origem=ingest.ORIGEM_EXTENSAO,
                               fonte=_fonte(lote.fonte), confianca=ingest.CONFIANCA_EXTENSAO)
    db.commit()
    return JSONResponse({"novos": res.novos, "ja_existiam": res.ja_existiam, "ruido": res.ruido,
                         "invalidos": res.invalidos, "limite": res.limite})

