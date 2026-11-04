from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.core.models import Department, IcpProfile
from app.web.deps import Ctx, current_ctx, editor_ctx, flash, render
from app.web.forms import necessidades_to_text, parse_department, parse_icp

router = APIRouter(prefix="/configuracoes")


@router.get("")
def hub(request: Request, ctx: Ctx = Depends(current_ctx)):
    return render(request, "config_hub.html", ctx, ativo="configuracoes")


# ---------- ICP ----------
def _get_icp(ctx: Ctx, icp_id: int) -> IcpProfile:
    icp = ctx.db.get(IcpProfile, icp_id)
    if icp is None or icp.workspace_id != ctx.workspace_id:  # isolamento entre workspaces
        raise HTTPException(status_code=404)
    return icp


def _depto_nomes(ctx: Ctx) -> list[str]:
    return list(ctx.db.scalars(select(Department.nome).where(Department.workspace_id == ctx.workspace_id).order_by(Department.nome)))


@router.get("/icp")
def icp_list(request: Request, ctx: Ctx = Depends(current_ctx)):
    rows = ctx.db.scalars(select(IcpProfile).where(IcpProfile.workspace_id == ctx.workspace_id).order_by(IcpProfile.nome)).all()
    return render(request, "icp_list.html", ctx, icps=rows, ativo="configuracoes")


@router.get("/icp/novo")
def icp_new(request: Request, ctx: Ctx = Depends(editor_ctx)):
    return render(request, "icp_form.html", ctx, icp=None, deptos=_depto_nomes(ctx), errors=[], ativo="configuracoes")


async def _icp_form(request: Request):
    f = await request.form()
    return parse_icp(
        f.get("nome", ""), f.get("cnaes", ""), f.get("ufs", ""), f.get("municipios", ""),
        f.get("portes", ""), f.get("termos_maps", ""), f.getlist("departamentos_alvo"),
    )


def _icp_error(request, ctx, data, errors, icp=None):
    shown = icp or IcpProfile(**data)
    for k, v in data.items():
        setattr(shown, k, v)
    return render(request, "icp_form.html", ctx, status=422, icp=shown, deptos=_depto_nomes(ctx),
                  errors=errors, ativo="configuracoes", editing=icp is not None)


@router.post("/icp")
async def icp_create(request: Request, ctx: Ctx = Depends(editor_ctx)):
    data, errors = await _icp_form(request)
    if errors:
        return _icp_error(request, ctx, data, errors)
    ctx.db.add(IcpProfile(workspace_id=ctx.workspace_id, **data))
    ctx.db.commit()
    flash(request, f"Perfil “{data['nome']}” criado.")
    return RedirectResponse("/configuracoes/icp", status_code=303)


@router.get("/icp/{icp_id}")
def icp_edit(icp_id: int, request: Request, ctx: Ctx = Depends(editor_ctx)):
    icp = _get_icp(ctx, icp_id)
    return render(request, "icp_form.html", ctx, icp=icp, deptos=_depto_nomes(ctx), errors=[],
                  ativo="configuracoes", editing=True)


@router.post("/icp/{icp_id}")
async def icp_update(icp_id: int, request: Request, ctx: Ctx = Depends(editor_ctx)):
    icp = _get_icp(ctx, icp_id)
    data, errors = await _icp_form(request)
    if errors:
        ctx.db.expire(icp)
        return _icp_error(request, ctx, data, errors, icp)
    for k, v in data.items():
        setattr(icp, k, v)
    ctx.db.commit()
    flash(request, "Perfil atualizado.")
    return RedirectResponse("/configuracoes/icp", status_code=303)


@router.post("/icp/{icp_id}/excluir")
def icp_delete(icp_id: int, request: Request, ctx: Ctx = Depends(editor_ctx)):
    icp = _get_icp(ctx, icp_id)
    ctx.db.delete(icp)
    ctx.db.commit()
    if request.headers.get("hx-request"):
        return HTMLResponse("")
    flash(request, "Perfil excluído.")
    return RedirectResponse("/configuracoes/icp", status_code=303)


# ---------- Departamentos ----------
def _get_dept(ctx: Ctx, dept_id: int) -> Department:
    d = ctx.db.get(Department, dept_id)
    if d is None or d.workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=404)
    return d


@router.get("/departamentos")
def dept_list(request: Request, ctx: Ctx = Depends(current_ctx)):
    rows = ctx.db.scalars(select(Department).where(Department.workspace_id == ctx.workspace_id).order_by(Department.nome)).all()
    return render(request, "dept_list.html", ctx, deptos=rows, ativo="configuracoes")


@router.get("/departamentos/novo")
def dept_new(request: Request, ctx: Ctx = Depends(editor_ctx)):
    return render(request, "dept_form.html", ctx, dept=None, needs_text="", errors=[], ativo="configuracoes")


async def _dept_form(request: Request):
    f = await request.form()
    needs = f.get("necessidades", "")
    data, errors = parse_department(f.get("nome", ""), f.get("email_patterns", ""), f.get("cargos", ""), needs)
    return data, errors, needs


def _dept_error(request, ctx, data, errors, needs, dept=None):
    shown = dept or Department(**data)
    for k, v in data.items():
        setattr(shown, k, v)
    return render(request, "dept_form.html", ctx, status=422, dept=shown, needs_text=needs, errors=errors,
                  ativo="configuracoes", editing=dept is not None)


def _name_taken(ctx: Ctx, nome: str, ignore_id: int | None = None) -> bool:
    d = ctx.db.scalar(select(Department).where(Department.workspace_id == ctx.workspace_id, Department.nome == nome))
    return d is not None and d.id != ignore_id


@router.post("/departamentos")
async def dept_create(request: Request, ctx: Ctx = Depends(editor_ctx)):
    data, errors, needs = await _dept_form(request)
    if not errors and _name_taken(ctx, data["nome"]):
        errors.append("Já existe um departamento com esse nome.")
    if errors:
        return _dept_error(request, ctx, data, errors, needs)
    ctx.db.add(Department(workspace_id=ctx.workspace_id, **data))
    ctx.db.commit()
    flash(request, f"Departamento “{data['nome']}” criado.")
    return RedirectResponse("/configuracoes/departamentos", status_code=303)


@router.get("/departamentos/{dept_id}")
def dept_edit(dept_id: int, request: Request, ctx: Ctx = Depends(editor_ctx)):
    d = _get_dept(ctx, dept_id)
    return render(request, "dept_form.html", ctx, dept=d, needs_text=necessidades_to_text(d.necessidades_previstas),
                  errors=[], ativo="configuracoes", editing=True)


@router.post("/departamentos/{dept_id}")
async def dept_update(dept_id: int, request: Request, ctx: Ctx = Depends(editor_ctx)):
    d = _get_dept(ctx, dept_id)
    data, errors, needs = await _dept_form(request)
    if not errors and _name_taken(ctx, data["nome"], d.id):
        errors.append("Já existe um departamento com esse nome.")
    if errors:
        ctx.db.expire(d)
        return _dept_error(request, ctx, data, errors, needs, d)
    for k, v in data.items():
        setattr(d, k, v)
    ctx.db.commit()
    flash(request, "Departamento atualizado.")
    return RedirectResponse("/configuracoes/departamentos", status_code=303)


@router.post("/departamentos/{dept_id}/excluir")
def dept_delete(dept_id: int, request: Request, ctx: Ctx = Depends(editor_ctx)):
    d = _get_dept(ctx, dept_id)
    ctx.db.delete(d)
    ctx.db.commit()
    if request.headers.get("hx-request"):
        return HTMLResponse("")
    flash(request, "Departamento excluído.")
    return RedirectResponse("/configuracoes/departamentos", status_code=303)
