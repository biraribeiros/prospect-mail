"""Campanha manual: escreva a mensagem num editor (Visual/Código), gere as mensagens, aprove e deixe o agendador enviar."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.models import Campaign, Contact, OutboundMessage
from app.disparo import campaign as camp
from app.disparo.compose import SPAM_MAX, spam_check
from app.disparo.htmlmail import MAX_BYTES, wrap_html
from app.web.deps import Ctx, current_ctx, editor_ctx, flash, render
from app.web.routes.filtro import ORIGEM_NOMES
from app.web.routes.inteligencia import approver_ctx
from app.workers.queue import enqueue

router = APIRouter(prefix="/disparo/campanhas")


def _get(ctx: Ctx, cid: int) -> Campaign:
    c = ctx.db.get(Campaign, cid)
    if c is None or c.workspace_id != ctx.workspace_id:  # isolamento entre workspaces
        raise HTTPException(status_code=404)
    return c


def _apply(c: Campaign, f) -> None:
    c.nome = (f.get("nome") or "").strip()[:120] or "Campanha sem nome"
    c.assunto = (f.get("assunto") or "").strip()[:200]
    formato = f.get("formato") if f.get("formato") in ("html", "texto") else "html"
    conteudo = f.get("corpo_texto_puro" if formato == "texto" else "corpo_html") or ""
    if len(conteudo) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="Mensagem grande demais.")
    camp.set_body(c, formato, conteudo)


@router.get("")
def lista(request: Request, ctx: Ctx = Depends(current_ctx)):
    cs = ctx.db.scalars(select(Campaign).where(Campaign.workspace_id == ctx.workspace_id).order_by(Campaign.id.desc())).all()
    cont: dict[int, dict[str, int]] = {}
    for cid, st, n in ctx.db.execute(select(OutboundMessage.campaign_id, OutboundMessage.status, func.count()).where(
            OutboundMessage.workspace_id == ctx.workspace_id, OutboundMessage.campaign_id.is_not(None))
            .group_by(OutboundMessage.campaign_id, OutboundMessage.status)):
        cont.setdefault(cid, {})[st] = n
    return render(request, "campanhas_lista.html", ctx, ativo="disparo", cs=cs, cont=cont)


@router.get("/nova")
def nova(request: Request, ctx: Ctx = Depends(editor_ctx)):
    c = Campaign(nome="", assunto="", formato="html", corpo_html="", corpo_texto="")
    return render(request, "campanha_editor.html", ctx, ativo="disparo", c=c, novo=True, variaveis=camp.VARIAVEIS, erros=[], avisos=[],
                  previa_html=None, previa_texto="", counts={}, msgs=[], origens=[], nomes=ORIGEM_NOMES, dry=get_settings().dry_run)


@router.post("")
async def criar(request: Request, ctx: Ctx = Depends(editor_ctx)):
    c = Campaign(workspace_id=ctx.workspace_id, criado_por=ctx.user.id, nome="")
    _apply(c, await request.form())
    ctx.db.add(c)
    ctx.db.commit()
    flash(request, "Campanha salva. Confira abaixo e gere as mensagens.")
    return RedirectResponse(f"/disparo/campanhas/{c.id}", status_code=303)


@router.get("/{cid:int}")
def detalhe(cid: int, request: Request, ctx: Ctx = Depends(current_ctx)):
    c = _get(ctx, cid)
    erros, avisos = camp.check_campaign(c)
    assunto, texto, html_, _ = camp.render(c, camp.sample_vars(ctx.workspace.nome))
    msgs = ctx.db.execute(select(OutboundMessage, Contact).join(Contact, Contact.id == OutboundMessage.contact_id).where(
        OutboundMessage.campaign_id == c.id, OutboundMessage.status != "descartada").order_by(OutboundMessage.id).limit(100)).all()
    return render(request, "campanha_editor.html", ctx, ativo="disparo", c=c, novo=False, variaveis=camp.VARIAVEIS, erros=erros,
                  avisos=avisos, previa_assunto=assunto, previa_html=wrap_html(html_) if html_ else None, previa_texto=texto,
                  counts=camp.status_counts(ctx.db, c), msgs=msgs, origens=camp.valid_by_origin(ctx.db, ctx.workspace_id),
                  nomes=ORIGEM_NOMES, dry=get_settings().dry_run)


@router.post("/{cid:int}")
async def salvar(cid: int, request: Request, ctx: Ctx = Depends(editor_ctx)):
    c = _get(ctx, cid)
    _apply(c, await request.form())
    ctx.db.commit()
    flash(request, "Campanha salva. Se mudou o texto, gere as mensagens de novo para valer nelas.")
    return RedirectResponse(f"/disparo/campanhas/{c.id}", status_code=303)


@router.post("/{cid:int}/conferir")
def conferir(cid: int, request: Request, ctx: Ctx = Depends(editor_ctx)):
    c = _get(ctx, cid)
    assunto, texto, html_, _ = camp.render(c, camp.sample_vars(ctx.workspace.nome))
    score, origem = spam_check(assunto, texto, ctx.workspace_id, html=html_)
    ok = score < SPAM_MAX
    flash(request, f"Nota de spam {score:.1f} ({'Rspamd' if origem == 'rspamd' else 'estimativa local, o Rspamd não respondeu'}): "
                   + ("boa, passa (precisa ser menor que 5)." if ok else "alta. Reescreva antes de aprovar."), "ok" if ok else "err")
    return RedirectResponse(f"/disparo/campanhas/{c.id}", status_code=303)


@router.post("/{cid:int}/gerar")
async def gerar(cid: int, request: Request, ctx: Ctx = Depends(editor_ctx)):
    c = _get(ctx, cid)
    f = await request.form()

    def num(k, d):
        v = str(f.get(k, "")).strip()
        return int(v) if v.isdigit() else d

    r = camp.generate(ctx.db, ctx.workspace_id, c, str(f.get("origem") or ""), num("limite", 40), num("max_dominio", 2))
    ctx.db.commit()
    extras = [f"{r['ja_contatados']} já contatados" if r["ja_contatados"] else "", f"{r['suprimidos']} na lista de supressão" if r["suprimidos"] else "",
              f"{r['excesso_dominio']} acima do limite por domínio" if r["excesso_dominio"] else ""]
    flash(request, f"{r['criadas']} mensagem(ns) em rascunho" + (f", {r['bloqueadas']} bloqueada(s) por variável vazia" if r["bloqueadas"] else "")
          + ("; ficaram de fora: " + ", ".join(e for e in extras if e) if any(extras) else "") + ".",
          "ok" if r["criadas"] else "err")
    return RedirectResponse(f"/disparo/campanhas/{c.id}", status_code=303)


@router.post("/{cid:int}/aprovar")
async def aprovar(cid: int, request: Request, ctx: Ctx = Depends(approver_ctx)):
    c = _get(ctx, cid)
    r = camp.approve(ctx.db, ctx.workspace_id, c)
    ctx.db.commit()
    if "erros" in r:
        for e in r["erros"]:
            flash(request, "Não aprovada — " + e, "err")
    else:
        flash(request, f"{r['aprovadas']} mensagem(ns) aprovada(s) (nota de spam {r['nota_spam']:.1f}). O agendador distribui os envios "
                       "pelos horários permitidos e pelo limite de cada caixa.")
        try:
            await enqueue("dispatch_workspace_job", ctx.workspace_id)
        except Exception:  # noqa: BLE001 - o agendador também roda sozinho a cada minuto
            pass
    return RedirectResponse(f"/disparo/campanhas/{c.id}", status_code=303)


@router.post("/{cid:int}/descartar")
def descartar(cid: int, request: Request, ctx: Ctx = Depends(editor_ctx)):
    c = _get(ctx, cid)
    n = camp.discard_drafts(ctx.db, c)
    ctx.db.commit()
    flash(request, f"{n} rascunho(s) descartado(s).")
    return RedirectResponse(f"/disparo/campanhas/{c.id}", status_code=303)
