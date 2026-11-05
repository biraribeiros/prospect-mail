import hashlib
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import exists, func, select

from app.core import csvio
from app.core.config import get_settings
from app.core.models import Contact, JobRun, MailboxAccount, MailInteraction
from app.core.security import decrypt_json, encrypt_json
from app.hunter.mailbox.factory import mbox_dir, validate_imap_host
from app.hunter.mailbox.imap import ImapAdapter
from app.hunter.mailbox.noise import DOMINIOS_RUIDO, LOCAL_PATTERNS
from app.web.deps import Ctx, current_ctx, editor_ctx, flash, render
from app.web.forms import split_list
from app.workers.queue import enqueue

router = APIRouter(prefix="/hunter")
PAGE = 50
PASTAS = {"misto": None, "recebidos": "recebido", "enviados": "enviado"}


def _get_mailbox(ctx: Ctx, mailbox_id: int) -> MailboxAccount:
    mb = ctx.db.get(MailboxAccount, mailbox_id)
    if mb is None or mb.workspace_id != ctx.workspace_id:  # isolamento entre workspaces
        raise HTTPException(status_code=404)
    return mb


def _cfg(mb: MailboxAccount) -> dict:
    return decrypt_json(mb.tokens_enc) if mb.tokens_enc else {}


def _last_job(ctx: Ctx, mailbox_id: int) -> JobRun | None:
    return ctx.db.scalar(
        select(JobRun).where(JobRun.workspace_id == ctx.workspace_id, JobRun.tipo == "scan_caixa",
                             JobRun.parametros["mailbox_id"].astext == str(mailbox_id))
        .order_by(JobRun.id.desc()).limit(1))


def _emails(*values: str) -> list[str]:
    out = []
    for v in values:
        for e in split_list(v):
            if "@" in e:
                out.append(e.lower())
    return list(dict.fromkeys(out))


async def _enqueue_scan(request: Request, mb: MailboxAccount) -> None:
    try:
        await enqueue("scan_mailbox_job", mb.id)
        flash(request, f"Varredura de “{mb.nome}” iniciada. Atualize a página em instantes.")
    except Exception:  # noqa: BLE001
        flash(request, "A fila de tarefas está indisponível agora. Tente novamente em instantes.", "err")


@router.get("")
def hunter_home(request: Request, ctx: Ctx = Depends(current_ctx)):
    ws = ctx.workspace_id
    boxes = ctx.db.scalar(select(func.count()).select_from(MailboxAccount).where(MailboxAccount.workspace_id == ws))
    contacts = ctx.db.scalar(select(func.count()).select_from(Contact).where(
        Contact.workspace_id == ws, Contact.qtd_enviados + Contact.qtd_recebidos > 0))
    return render(request, "hunter_home.html", ctx, ativo="hunter", boxes=boxes, contacts=contacts)


# ---------- caixas ----------
@router.get("/caixas")
def caixas(request: Request, ctx: Ctx = Depends(current_ctx)):
    rows = ctx.db.scalars(select(MailboxAccount).where(MailboxAccount.workspace_id == ctx.workspace_id)
                          .order_by(MailboxAccount.id)).all()
    items = [{"mb": mb, "job": _last_job(ctx, mb.id), "emails": _cfg(mb).get("emails", [])} for mb in rows]
    return render(request, "hunter_caixas.html", ctx, ativo="hunter", items=items)


async def _save_upload(file: UploadFile, dest: Path, max_bytes: int) -> tuple[Path, str]:
    dest.mkdir(parents=True, exist_ok=True)
    tmp, h, size = dest / f".tmp-{uuid4().hex}", hashlib.sha256(), 0
    try:
        with tmp.open("wb") as out:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(status_code=413, detail="Arquivo grande demais.")
                h.update(chunk)
                out.write(chunk)
        ext = ".eml" if (file.filename or "").lower().endswith(".eml") else ".mbox"
        final = dest / f"{h.hexdigest()}{ext}"
        if final.exists():
            tmp.unlink()
        else:
            tmp.replace(final)
        return final, h.hexdigest()
    finally:
        tmp.unlink(missing_ok=True)


@router.post("/caixas/mbox")
async def add_mbox(request: Request, file: UploadFile = File(...), nome: str = Form(""),
                   email: str = Form(""), pasta: str = Form("misto"), ctx: Ctx = Depends(editor_ctx)):
    emails = _emails(email)
    if not emails:
        flash(request, "Informe o seu email (o dono da caixa) para o sistema não tratá-lo como contato.", "err")
        return RedirectResponse("/hunter/caixas", status_code=303)
    if pasta not in PASTAS:
        raise HTTPException(status_code=422, detail="Pasta inválida.")
    path, sha = await _save_upload(file, mbox_dir(ctx.workspace_id), get_settings().mbox_max_mb * 1024 * 1024)
    for other in ctx.db.scalars(select(MailboxAccount).where(MailboxAccount.workspace_id == ctx.workspace_id,
                                                            MailboxAccount.tipo == "mbox")):
        if _cfg(other).get("sha") == sha:
            flash(request, f"Este arquivo já foi enviado como “{other.nome}”.", "err")
            return RedirectResponse("/hunter/caixas", status_code=303)
    mb = MailboxAccount(
        workspace_id=ctx.workspace_id, tipo="mbox", nome=(nome.strip() or file.filename or "Arquivo")[:80],
        dominio=emails[0].rsplit("@", 1)[-1],
        tokens_enc=encrypt_json({"path": str(path), "sha": sha, "pasta_hint": PASTAS[pasta], "emails": emails}))
    ctx.db.add(mb)
    ctx.db.commit()
    await _enqueue_scan(request, mb)
    return RedirectResponse("/hunter/caixas", status_code=303)


@router.post("/caixas/imap")
async def add_imap(request: Request, nome: str = Form(""), host: str = Form(""), port: int = Form(993),
                   user: str = Form(""), password: str = Form(""), security: str = Form("ssl"),
                   email: str = Form(""), ctx: Ctx = Depends(editor_ctx)):
    host, user = host.strip(), user.strip()
    erro = validate_imap_host(host)
    if erro is None and security not in ("ssl", "starttls"):
        erro = "Use conexão segura (SSL ou STARTTLS)."
    if erro is None and not (1 <= port <= 65535 and user and password):
        erro = "Preencha servidor, porta, usuário e senha."
    emails = _emails(email, user)
    if erro is None and not emails:
        erro = "Informe o seu endereço de email."
    if erro is None:
        try:
            await run_in_threadpool(ImapAdapter(host, port, user, password, security).test_connection)
        except Exception:  # noqa: BLE001 - não ecoamos detalhes do servidor
            erro = "Não consegui entrar nessa caixa. Confira servidor, porta, usuário e senha."
    if erro:
        flash(request, erro, "err")
        return RedirectResponse("/hunter/caixas", status_code=303)
    mb = MailboxAccount(
        workspace_id=ctx.workspace_id, tipo="imap", nome=(nome.strip() or user)[:80],
        dominio=emails[0].rsplit("@", 1)[-1],
        tokens_enc=encrypt_json({"host": host, "port": port, "user": user, "password": password,
                                 "security": security, "emails": emails}))
    ctx.db.add(mb)
    ctx.db.commit()
    await _enqueue_scan(request, mb)
    return RedirectResponse("/hunter/caixas", status_code=303)


@router.post("/caixas/{mailbox_id}/varrer")
async def scan_now(mailbox_id: int, request: Request, ctx: Ctx = Depends(editor_ctx)):
    mb = _get_mailbox(ctx, mailbox_id)
    await _enqueue_scan(request, mb)
    return RedirectResponse("/hunter/caixas", status_code=303)


@router.post("/caixas/{mailbox_id}/excluir")
def delete_mailbox(mailbox_id: int, request: Request, ctx: Ctx = Depends(editor_ctx)):
    mb = _get_mailbox(ctx, mailbox_id)
    cfg = _cfg(mb)
    ctx.db.delete(mb)
    ctx.db.commit()
    if mb.tipo == "mbox" and cfg.get("path"):
        Path(cfg["path"]).unlink(missing_ok=True)
    flash(request, "Caixa removida. Os contatos já encontrados continuam na lista.")
    return RedirectResponse("/hunter/caixas", status_code=303)


# ---------- contatos ----------
def _filters(q: str, caixa: int | None, tipo: str, minimo: int):
    conds = [Contact.qtd_enviados + Contact.qtd_recebidos > 0]
    if q.strip():
        conds.append(Contact.email.icontains(q.strip(), autoescape=True)
                     | Contact.nome_completo.icontains(q.strip(), autoescape=True))
    if caixa:
        conds.append(exists().where(MailInteraction.contact_id == Contact.id, MailInteraction.mailbox_id == caixa))
    if tipo in ("corporativo", "pessoal"):
        conds.append(Contact.dominio_tipo == tipo)
    if minimo > 0:
        conds.append(Contact.relationship_score >= minimo)
    return conds


@router.get("/contatos")
def contatos(request: Request, q: str = "", caixa: int | None = None, tipo: str = "", minimo: int = 0,
             pagina: int = 1, ctx: Ctx = Depends(current_ctx)):
    conds = _filters(q, caixa, tipo, minimo)
    base = select(Contact).where(Contact.workspace_id == ctx.workspace_id, *conds)
    total = ctx.db.scalar(select(func.count()).select_from(base.subquery()))
    pagina = max(1, pagina)
    rows = ctx.db.scalars(base.order_by(Contact.relationship_score.desc().nullslast(), Contact.id)
                          .limit(PAGE).offset((pagina - 1) * PAGE)).all()
    caixas_ws = ctx.db.scalars(select(MailboxAccount).where(MailboxAccount.workspace_id == ctx.workspace_id)).all()
    keep = {k: v for k, v in {"q": q, "caixa": caixa or "", "tipo": tipo, "minimo": minimo or ""}.items() if v != ""}
    return render(request, "hunter_contatos.html", ctx, ativo="hunter", rows=rows, total=total, pagina=pagina,
                  paginas=max(1, -(-total // PAGE)), caixas=caixas_ws, f={"q": q, "caixa": caixa, "tipo": tipo, "minimo": minimo},
                  qs=urlencode(keep))


@router.get("/contatos.csv")
def contatos_csv(q: str = "", caixa: int | None = None, tipo: str = "", minimo: int = 0,
                 ctx: Ctx = Depends(current_ctx)):
    header = ["nome_completo", "email", "cargo", "telefone", "site", "dominio_tipo", "qtd_enviados",
              "qtd_recebidos", "ultima_interacao", "nota_relacionamento"]
    stmt = (select(Contact).where(Contact.workspace_id == ctx.workspace_id, *_filters(q, caixa, tipo, minimo))
            .order_by(Contact.relationship_score.desc().nullslast(), Contact.id))
    rows = ([c.nome_completo, c.email, c.cargo, c.telefone, c.site, c.dominio_tipo, c.qtd_enviados,
             c.qtd_recebidos, c.ultima_interacao.date().isoformat() if c.ultima_interacao else "",
             c.relationship_score] for c in ctx.db.scalars(stmt))
    return StreamingResponse(csvio.csv_stream(header, rows), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="contatos_caixas.csv"'})


# ---------- filtro de ruído ----------
@router.get("/ruido")
def ruido(request: Request, ctx: Ctx = Depends(current_ctx)):
    cfg = (ctx.workspace.config or {}).get("ruido", {})
    return render(request, "hunter_ruido.html", ctx, ativo="hunter", cfg=cfg,
                  padroes=LOCAL_PATTERNS, dominios_padrao=DOMINIOS_RUIDO)


@router.post("/ruido")
async def ruido_save(request: Request, ctx: Ctx = Depends(editor_ctx)):
    f = await request.form()
    novo = {
        "locais": split_list(f.get("locais", "")),
        "dominios": [d.lower() for d in split_list(f.get("dominios", ""))],
        "permitidos": [d.lower() for d in split_list(f.get("permitidos", ""))],
    }
    ctx.workspace.config = {**(ctx.workspace.config or {}), "ruido": novo}
    ctx.db.commit()
    flash(request, "Filtro de ruído salvo. Vale para as próximas varreduras.")
    return RedirectResponse("/hunter/ruido", status_code=303)
