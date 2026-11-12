"""Telas da Inteligência (Etapa 5b): ofertas, hipóteses de oportunidade, fila de aprovação e Top 50."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from app.core.models import Company, Contact, Department, Hypothesis, JobRun, Offer
from app.intel.defaults import OFERTAS_MODELO
from app.intel.hypothesis import decide
from app.web.deps import Ctx, current_ctx, editor_ctx, flash, render
from app.web.forms import split_list
from app.web.routes.empresas import NOMES, _queue

router = APIRouter(prefix="/inteligencia")
TIPOS_SINAL = ["vaga", "expansao", "inauguracao", "lancamento", "diretoria", "feira", "maps_ativo"]


def approver_ctx(ctx: Ctx = Depends(current_ctx)) -> Ctx:
    if ctx.papel not in ("admin", "aprovador"):
        raise HTTPException(status_code=403, detail="Só administradores e aprovadores decidem hipóteses.")
    return ctx


def _offer(ctx: Ctx, offer_id: int) -> Offer:
    o = ctx.db.get(Offer, offer_id)
    if o is None or o.workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=404)
    return o


def _hyp_rows(ctx: Ctx, status: str | None, limit: int):
    q = (select(Hypothesis, Company, Contact, Offer).join(Company, Company.id == Hypothesis.company_id)
         .join(Contact, Contact.id == Hypothesis.contact_id).join(Offer, Offer.id == Hypothesis.offer_id)
         .where(Hypothesis.workspace_id == ctx.workspace_id))
    if status:
        q = q.where(Hypothesis.status == status)
    return ctx.db.execute(q.order_by(Hypothesis.nota.desc(), Hypothesis.id).limit(limit)).all()


@router.get("")
def home(request: Request, ctx: Ctx = Depends(current_ctx)):
    cont = dict(ctx.db.execute(select(Hypothesis.status, func.count()).where(
        Hypothesis.workspace_id == ctx.workspace_id).group_by(Hypothesis.status)).all())
    n_ofertas = ctx.db.scalar(select(func.count()).select_from(Offer).where(Offer.workspace_id == ctx.workspace_id))
    jobs = list(ctx.db.scalars(select(JobRun).where(JobRun.workspace_id == ctx.workspace_id, JobRun.tipo == "hipoteses")
                               .order_by(JobRun.id.desc()).limit(5)))
    return render(request, "intel_home.html", ctx, ativo="inteligencia", cont=cont, n_ofertas=n_ofertas, jobs=jobs, nomes=NOMES,
                  top=_hyp_rows(ctx, None, 50))


@router.post("/gerar")
async def gerar(request: Request, ctx: Ctx = Depends(editor_ctx)):
    await _queue(request, "hypotheses_job", ctx.workspace_id, ok="Geração de hipóteses iniciada.")
    return RedirectResponse("/inteligencia", status_code=303)


@router.get("/hipoteses")
def fila(request: Request, status: str = "pendente", ctx: Ctx = Depends(current_ctx)):
    return render(request, "intel_hipoteses.html", ctx, ativo="inteligencia", rows=_hyp_rows(ctx, status or None, 200), status_f=status)


@router.post("/hipoteses/{hyp_id:int}/decidir")
def decidir(hyp_id: int, request: Request, acao: str = Form(...), texto: str = Form(""), ctx: Ctx = Depends(approver_ctx)):
    if acao not in ("aprovar", "rejeitar"):
        raise HTTPException(status_code=422)
    try:
        decide(ctx.db, ctx.workspace_id, hyp_id, ctx.user.id, acao == "aprovar", texto)
    except LookupError:
        raise HTTPException(status_code=404)
    ctx.db.commit()
    flash(request, "Hipótese aprovada." if acao == "aprovar" else "Hipótese rejeitada.")
    return RedirectResponse("/inteligencia/hipoteses", status_code=303)


# ---------- ofertas ----------
def _parse_offer(f) -> tuple[dict, list[str]]:
    nome = (f.get("nome") or "").strip()
    errors = [] if nome else ["Informe o nome da oferta."]
    tipos = [t for t in f.getlist("tipos_sinal") if t in TIPOS_SINAL]
    if not tipos:
        errors.append("Escolha ao menos um tipo de sinal que torna esta oferta relevante.")
    cases = [c.strip()[:300] for c in (f.get("cases") or "").splitlines() if c.strip()][:10]
    return {"nome": nome[:120], "descricao": (f.get("descricao") or "").strip()[:600], "faixa_preco": (f.get("faixa_preco") or "").strip()[:80] or None,
            "cases": cases, "tags": [t.lower() for t in split_list(f.get("tags") or "")], "tipos_sinal": tipos,
            "departamentos": split_list(f.get("departamentos") or ""), "ativo": f.get("ativo") == "sim"}, errors


@router.get("/ofertas")
def ofertas(request: Request, editar: int = 0, ctx: Ctx = Depends(current_ctx)):
    rows = ctx.db.scalars(select(Offer).where(Offer.workspace_id == ctx.workspace_id).order_by(Offer.nome)).all()
    depts = ctx.db.scalars(select(Department.nome).where(Department.workspace_id == ctx.workspace_id).order_by(Department.nome)).all()
    return render(request, "intel_ofertas.html", ctx, ativo="inteligencia", rows=rows, tipos=TIPOS_SINAL, depts=depts,
                  edit=_offer(ctx, editar) if editar else None, errors=[])


@router.post("/ofertas")
async def oferta_salvar(request: Request, ctx: Ctx = Depends(editor_ctx)):
    f = await request.form()
    data, errors = _parse_offer(f)
    oid = int(f.get("id") or 0)
    if not errors:
        dup = ctx.db.scalar(select(Offer.id).where(Offer.workspace_id == ctx.workspace_id, Offer.nome == data["nome"], Offer.id != oid))
        if dup:
            errors.append("Já existe uma oferta com este nome.")
    if errors:
        rows = ctx.db.scalars(select(Offer).where(Offer.workspace_id == ctx.workspace_id).order_by(Offer.nome)).all()
        depts = ctx.db.scalars(select(Department.nome).where(Department.workspace_id == ctx.workspace_id)).all()
        return render(request, "intel_ofertas.html", ctx, status=422, ativo="inteligencia", rows=rows, tipos=TIPOS_SINAL,
                      depts=depts, edit=None, errors=errors)
    o = _offer(ctx, oid) if oid else Offer(workspace_id=ctx.workspace_id)
    for k, v in data.items():
        setattr(o, k, v)
    ctx.db.add(o)
    ctx.db.commit()
    flash(request, "Oferta salva.")
    return RedirectResponse("/inteligencia/ofertas", status_code=303)


@router.post("/ofertas/modelos")
def oferta_modelos(request: Request, ctx: Ctx = Depends(editor_ctx)):
    existentes = set(ctx.db.scalars(select(Offer.nome).where(Offer.workspace_id == ctx.workspace_id)))
    novas = 0
    for m in OFERTAS_MODELO:
        if m["nome"] not in existentes:
            ctx.db.add(Offer(workspace_id=ctx.workspace_id, cases=[], departamentos=[], **m))
            novas += 1
    ctx.db.commit()
    flash(request, f"{novas} modelo(s) de oferta criados. Edite com seus serviços, preços e cases reais.")
    return RedirectResponse("/inteligencia/ofertas", status_code=303)


@router.post("/ofertas/{offer_id:int}/excluir")
def oferta_excluir(offer_id: int, request: Request, ctx: Ctx = Depends(editor_ctx)):
    ctx.db.delete(_offer(ctx, offer_id))
    ctx.db.commit()
    flash(request, "Oferta excluída (as hipóteses ligadas a ela também).")
    return RedirectResponse("/inteligencia/ofertas", status_code=303)
