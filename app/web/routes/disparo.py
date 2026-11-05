"""Telas do Disparo: caixas e domínios de envio (6a), agenda e disjuntor (6b), mensagens e aprovação (7), respostas e CRM (8)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from app.core.models import (Company, Contact, MailboxAccount, MessageTemplate, Offer, OutboundMessage, SenderDomain, Suppression)
from app.core.security import decrypt_json, encrypt_json
from app.disparo import breaker
from app.disparo.compose import approve_sequence, discard_sequence
from app.disparo.dnscheck import RealDns, preflight
from app.disparo.guard import MainDomainError, domain_of_address, main_domains, norm_domain, register_sender_domain
from app.disparo.scheduler import FASES_AQUECIMENTO, used_today, warmup_limit
from app.hunter.mailbox.factory import validate_imap_host
from app.web.deps import Ctx, current_ctx, editor_ctx, flash, render
from app.web.routes.inteligencia import approver_ctx
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


# ---------- Etapa 7: mensagens ----------
@router.get("/mensagens")
def mensagens(request: Request, ctx: Ctx = Depends(current_ctx)):
    ws = ctx.workspace_id
    firsts = ctx.db.execute(select(OutboundMessage, Contact, Company).join(Contact, Contact.id == OutboundMessage.contact_id)
                            .outerjoin(Company, Company.id == OutboundMessage.company_id)
                            .where(OutboundMessage.workspace_id == ws, OutboundMessage.passo == 1,
                                   OutboundMessage.status.in_(("rascunho", "bloqueada")))
                            .order_by(OutboundMessage.id).limit(100)).all()
    ids = [m.id for m, _, _ in firsts]
    seq = {}
    for m in ctx.db.scalars(select(OutboundMessage).where(OutboundMessage.thread_raiz_id.in_(ids or [0])).order_by(OutboundMessage.passo)):
        seq.setdefault(m.thread_raiz_id, []).append(m)
    return render(request, "disparo_mensagens.html", ctx, ativo="disparo", firsts=firsts, seq=seq)


@router.post("/mensagens/gerar")
async def mensagens_gerar(request: Request, ctx: Ctx = Depends(editor_ctx)):
    try:
        await enqueue("compose_job", ctx.workspace_id)
        flash(request, "Escrevendo mensagens para as hipóteses aprovadas. Recarregue em instantes.")
    except Exception:  # noqa: BLE001
        flash(request, "A fila de tarefas está indisponível agora. Tente novamente em instantes.", "err")
    return RedirectResponse("/disparo/mensagens", status_code=303)


@router.post("/mensagens/decidir")
async def mensagens_decidir(request: Request, ctx: Ctx = Depends(approver_ctx)):
    f = await request.form()
    acao = f.get("acao")
    marcados = [int(x) for x in f.getlist("sel") if str(x).isdigit()]
    if acao not in ("aprovar", "descartar") or not marcados:
        flash(request, "Marque ao menos uma mensagem.", "err")
        return RedirectResponse("/disparo/mensagens", status_code=303)
    ok, falhas = 0, []
    for fid in marcados:
        try:
            if acao == "descartar":
                discard_sequence(ctx.db, ctx.workspace_id, fid)
                ok += 1
                continue
            edits = {}
            for m in ctx.db.scalars(select(OutboundMessage).where(OutboundMessage.workspace_id == ctx.workspace_id,
                                                                  OutboundMessage.thread_raiz_id == fid)):
                if f.get(f"assunto_{m.id}") is not None:
                    edits[m.id] = (f.get(f"assunto_{m.id}", m.assunto), f.get(f"corpo_{m.id}", m.corpo))
            erros = approve_sequence(ctx.db, ctx.workspace_id, fid, edits)
            if erros:
                falhas.append(f"#{fid}: " + "; ".join(erros[:2]))
            else:
                ok += 1
        except LookupError:
            continue
    ctx.db.commit()
    if ok:
        flash(request, f"{ok} sequência(s) {'aprovada(s)' if acao == 'aprovar' else 'descartada(s)'}.")
    for e in falhas:
        flash(request, "Não aprovada — " + e, "err")
    return RedirectResponse("/disparo/mensagens", status_code=303)


@router.get("/modelos")
def modelos(request: Request, ctx: Ctx = Depends(current_ctx)):
    rows = ctx.db.scalars(select(MessageTemplate).where(MessageTemplate.workspace_id == ctx.workspace_id)
                          .order_by(MessageTemplate.objetivo, MessageTemplate.nome)).all()
    return render(request, "disparo_modelos.html", ctx, ativo="disparo", rows=rows,
                  variaveis=["primeiro_nome", "empresa", "frase_sinal", "contexto", "oferta_frase", "prova", "pedido", "remetente", "assunto_original"])


@router.post("/modelos")
async def modelo_salvar(request: Request, ctx: Ctx = Depends(editor_ctx)):
    f = await request.form()
    nome, obj = (f.get("nome") or "").strip()[:80], f.get("objetivo")
    assunto, corpo = (f.get("assunto") or "").strip()[:200], (f.get("corpo") or "").strip()
    if not nome or obj not in ("sonda", "seguimento", "ultimo_toque") or not assunto or not corpo:
        flash(request, "Preencha nome, objetivo, assunto e texto.", "err")
    elif ctx.db.scalar(select(MessageTemplate.id).where(MessageTemplate.workspace_id == ctx.workspace_id, MessageTemplate.nome == nome)):
        flash(request, "Já existe um modelo com esse nome.", "err")
    else:
        ctx.db.add(MessageTemplate(workspace_id=ctx.workspace_id, nome=nome, objetivo=obj, assunto=assunto, corpo=corpo,
                                   departamento=(f.get("departamento") or "").strip() or None))
        ctx.db.commit()
        flash(request, "Modelo salvo.")
    return RedirectResponse("/disparo/modelos", status_code=303)


@router.post("/modelos/{tid:int}/excluir")
def modelo_excluir(tid: int, request: Request, ctx: Ctx = Depends(editor_ctx)):
    t = ctx.db.get(MessageTemplate, tid)
    if t is None or t.workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=404)
    ctx.db.delete(t)
    ctx.db.commit()
    flash(request, "Modelo excluído.")
    return RedirectResponse("/disparo/modelos", status_code=303)


__all__ = ["router", "Company", "Suppression", "norm_domain", "Offer"]
