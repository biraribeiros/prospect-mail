"""Telas do Disparo: caixas e domínios de envio (6a), agenda e disjuntor (6b), mensagens e aprovação (7), respostas e CRM (8)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from app.core.models import Company, Contact, MailboxAccount, OutboundMessage, SenderDomain, Suppression
from app.core.security import decrypt_json, encrypt_json
from app.disparo import breaker
from app.disparo.dnscheck import RealDns, preflight
from app.disparo.guard import MainDomainError, domain_of_address, main_domains, norm_domain, register_sender_domain
from app.disparo.scheduler import FASES_AQUECIMENTO, used_today, warmup_limit
from app.hunter.mailbox.factory import validate_imap_host
from app.web.deps import Ctx, current_ctx, editor_ctx, flash, render
from app.workers.queue import enqueue

router = APIRouter(prefix="/disparo")
PRESETS = {"gmail": ("smtp.gmail.com", 587, "starttls"), "microsoft": ("smtp.office365.com", 587, "starttls"),
           "outro": ("", 587, "starttls")}


def admin_ctx(ctx: Ctx = Depends(current_ctx)) -> Ctx:
    if ctx.papel != "admin":
        raise HTTPException(status_code=403, detail="Só administradores podem fazer isso.")
    return ctx


def _box(ctx: Ctx, box_id: int) -> MailboxAccount:
    mb = ctx.db.get(MailboxAccount, box_id)
    if mb is None or mb.workspace_id != ctx.workspace_id or mb.uso not in ("envio", "ambos"):
        raise HTTPException(status_code=404)
    return mb


@router.get("")
def home(request: Request, ctx: Ctx = Depends(current_ctx)):
    ws, now = ctx.workspace_id, datetime.now(timezone.utc)
    boxes = ctx.db.scalars(select(MailboxAccount).where(MailboxAccount.workspace_id == ws, MailboxAccount.uso.in_(("envio", "ambos")))
                           .order_by(MailboxAccount.id)).all()
    domains = {d.id: d for d in ctx.db.scalars(select(SenderDomain).where(SenderDomain.workspace_id == ws))}
    info = {mb.id: (used_today(ctx.db, mb.id, now), warmup_limit(ctx.db, mb, now), decrypt_json(mb.tokens_enc).get("from", ""))
            for mb in boxes}
    fila = dict(ctx.db.execute(select(OutboundMessage.status, func.count()).where(OutboundMessage.workspace_id == ws)
                               .group_by(OutboundMessage.status)).all())
    prox = ctx.db.execute(select(OutboundMessage, Contact).join(Contact, Contact.id == OutboundMessage.contact_id).where(
        OutboundMessage.workspace_id == ws, OutboundMessage.status == "agendada").order_by(OutboundMessage.agendada_para).limit(15)).all()
    sent, bounced, rate = breaker.rates(ctx.db, ws, now)
    return render(request, "disparo_home.html", ctx, ativo="disparo", boxes=boxes, domains=domains, info=info, fila=fila, prox=prox,
                  pausa=breaker.pause_info(ctx.db, ws), enviadas24=sent, bounces24=bounced, taxa24=rate,
                  principais=sorted(main_domains(ctx.db, ws)), plano=FASES_AQUECIMENTO, presets=PRESETS)


@router.post("/caixas")
async def caixa_criar(request: Request, ctx: Ctx = Depends(editor_ctx)):
    f = await request.form()
    preset = f.get("preset", "outro")
    p_host, p_port, p_sec = PRESETS.get(preset, PRESETS["outro"])
    email = (f.get("email") or "").strip().lower()
    host = (f.get("host") or p_host).strip()
    try:
        port = int(f.get("port") or p_port)
    except ValueError:
        port = 0
    sec = f.get("security") or p_sec
    errors = []
    if "@" not in email:
        errors.append("Informe o endereço de email da caixa (ex.: contato@seudominio-secundario.com.br).")
    if (msg := validate_imap_host(host)):
        errors.append(msg.replace("IMAP", "SMTP"))
    if not 1 <= port <= 65535 or sec not in ("ssl", "starttls"):
        errors.append("Porta ou tipo de segurança inválidos.")
    if not (f.get("user") or email) or not f.get("password"):
        errors.append("Informe o usuário e a senha da caixa.")
    nome = (f.get("nome") or email).strip()[:80]
    if errors:
        for e in errors:
            flash(request, e, "err")
        return RedirectResponse("/disparo", status_code=303)
    try:
        dom = register_sender_domain(ctx.db, ctx.workspace_id, domain_of_address(email), "secundario", (f.get("selector") or "").strip() or None)
    except (MainDomainError, ValueError) as e:
        ctx.db.rollback()
        flash(request, str(e), "err")
        return RedirectResponse("/disparo", status_code=303)
    try:
        limite = max(0, int(f.get("limite") or 0))
    except ValueError:
        limite = 0
    cfg = {"host": host, "port": port, "security": sec, "user": (f.get("user") or email).strip(), "password": f.get("password"),
           "from": email, "from_name": (f.get("from_name") or "").strip()[:80] or None}
    ctx.db.add(MailboxAccount(workspace_id=ctx.workspace_id, tipo="smtp", nome=nome, uso="envio", dominio=dom.nome,
                              tokens_enc=encrypt_json(cfg), limite_diario=limite, sender_domain_id=dom.id))
    ctx.db.commit()
    flash(request, "Caixa cadastrada. Agora confira o DNS do domínio: o envio só libera depois que tudo estiver certo.")
    return RedirectResponse("/disparo", status_code=303)


@router.post("/dominios/{dom_id:int}/checar")
def dominio_checar(dom_id: int, request: Request, selector: str = Form(""), ctx: Ctx = Depends(editor_ctx)):
    d = ctx.db.get(SenderDomain, dom_id)
    if d is None or d.workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=404)
    box = ctx.db.scalar(select(MailboxAccount).where(MailboxAccount.sender_domain_id == d.id))
    smtp_host = decrypt_json(box.tokens_enc).get("host") if box and box.tokens_enc else None
    if selector.strip():
        d.dkim_selector = selector.strip()[:60]
    rep = preflight(d.nome, d.dkim_selector, smtp_host, RealDns())
    d.spf_ok, d.dkim_ok, d.dmarc_ok, d.rdns_ok = rep.spf_ok, rep.dkim_ok, rep.dmarc_ok, rep.rdns_ok
    d.detalhes, d.checado_em = rep.detalhes, datetime.now(timezone.utc)
    ctx.db.commit()
    flash(request, "DNS conferido: tudo certo." if rep.pronto else "DNS com pendências: " + "; ".join(rep.problemas), "ok" if rep.pronto else "err")
    return RedirectResponse("/disparo", status_code=303)


@router.post("/caixas/{box_id:int}/pausar")
def caixa_pausar(box_id: int, request: Request, ctx: Ctx = Depends(editor_ctx)):
    mb = _box(ctx, box_id)
    mb.pausada = not mb.pausada
    ctx.db.commit()
    flash(request, "Caixa pausada." if mb.pausada else "Caixa reativada.")
    return RedirectResponse("/disparo", status_code=303)


@router.post("/caixas/{box_id:int}/excluir")
def caixa_excluir(box_id: int, request: Request, ctx: Ctx = Depends(editor_ctx)):
    mb = _box(ctx, box_id)
    if ctx.db.scalar(select(OutboundMessage.id).where(OutboundMessage.mailbox_id == mb.id).limit(1)):
        mb.pausada = True
        ctx.db.commit()
        flash(request, "Esta caixa já enviou mensagens: ela foi pausada (o histórico é mantido).", "err")
    else:
        ctx.db.delete(mb)
        ctx.db.commit()
        flash(request, "Caixa removida.")
    return RedirectResponse("/disparo", status_code=303)


@router.post("/liberar")
def liberar(request: Request, ctx: Ctx = Depends(admin_ctx)):
    breaker.resume(ctx.db, ctx.workspace_id)
    ctx.db.commit()
    flash(request, "Envio liberado. Acompanhe a taxa de bounces nas próximas horas.")
    return RedirectResponse("/disparo", status_code=303)


@router.post("/despachar")
async def despachar(request: Request, ctx: Ctx = Depends(editor_ctx)):
    try:
        await enqueue("dispatch_workspace_job", ctx.workspace_id)
        flash(request, "Agendamento e envio acionados.")
    except Exception:  # noqa: BLE001
        flash(request, "A fila de tarefas está indisponível agora. Tente novamente em instantes.", "err")
    return RedirectResponse("/disparo", status_code=303)


__all__ = ["router", "Company", "Suppression", "norm_domain"]
