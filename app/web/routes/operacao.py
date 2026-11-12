"""Etapa 10: Operação — limites, saúde (backups, tarefas presas, tokens), usuários e novos workspaces. Só administradores."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.core import limits
from app.core.models import AppUser, MailboxAccount, Workspace, WorkspaceMember
from app.core.security import hash_password
from app.ops import health
from app.web.deps import Ctx, flash, render
from app.web.routes.disparo import admin_ctx

router = APIRouter(prefix="/operacao")


@router.get("")
def home(request: Request, ctx: Ctx = Depends(admin_ctx)):
    ws = ctx.workspace_id
    membros = ctx.db.execute(select(AppUser, WorkspaceMember).join(WorkspaceMember, WorkspaceMember.user_id == AppUser.id)
                             .where(WorkspaceMember.workspace_id == ws).order_by(AppUser.email)).all()
    caixas = ctx.db.scalars(select(MailboxAccount).where(MailboxAccount.workspace_id == ws, MailboxAccount.tipo.in_(("gmail", "graph")))).all()
    return render(request, "operacao.html", ctx, ativo="configuracoes", lim=limits.get_limits(ctx.db, ws), uso=limits.usage(ctx.db, ws),
                  backup=health.backup_status(), presos=[j for j in health.stuck_jobs(ctx.db) if j.workspace_id == ws], membros=membros,
                  caixas=caixas)


@router.post("/limites")
async def salvar_limites(request: Request, ctx: Ctx = Depends(admin_ctx)):
    f = await request.form()
    try:
        limits.set_limits(ctx.db, ctx.workspace_id, {k: f.get(k) for k in limits.CAMPOS})
        ctx.db.commit()
        flash(request, "Limites salvos. Use 0 para não limitar.")
    except ValueError as e:
        ctx.db.rollback()
        flash(request, str(e), "err")
    return RedirectResponse("/operacao", status_code=303)


@router.post("/usuarios")
def novo_usuario(request: Request, email: str = Form(...), nome: str = Form(""), senha: str = Form(...), papel: str = Form("operador"),
                 ctx: Ctx = Depends(admin_ctx)):
    email = email.strip().lower()
    if papel not in ("admin", "operador", "aprovador") or "@" not in email or len(senha) < 10:
        flash(request, "Informe um email válido, um papel válido e uma senha com pelo menos 10 caracteres.", "err")
        return RedirectResponse("/operacao", status_code=303)
    u = ctx.db.scalar(select(AppUser).where(AppUser.email == email))
    if u is None:
        u = AppUser(email=email, nome=nome.strip()[:80], senha_hash=hash_password(senha))
        ctx.db.add(u)
        ctx.db.flush()
    if ctx.db.scalar(select(WorkspaceMember.id).where(WorkspaceMember.user_id == u.id, WorkspaceMember.workspace_id == ctx.workspace_id)):
        flash(request, "Este usuário já faz parte do workspace.", "err")
    else:
        ctx.db.add(WorkspaceMember(user_id=u.id, workspace_id=ctx.workspace_id, papel=papel))
        flash(request, f"Usuário {email} adicionado como {papel}.")
    ctx.db.commit()
    return RedirectResponse("/operacao", status_code=303)


@router.post("/workspaces")
def novo_workspace(request: Request, nome: str = Form(...), ctx: Ctx = Depends(admin_ctx)):
    nome = nome.strip()[:80]
    if not nome or ctx.db.scalar(select(Workspace.id).where(Workspace.nome == nome)):
        flash(request, "Informe um nome novo para o workspace.", "err")
        return RedirectResponse("/operacao", status_code=303)
    w = Workspace(nome=nome)
    ctx.db.add(w)
    ctx.db.flush()
    ctx.db.add(WorkspaceMember(user_id=ctx.user.id, workspace_id=w.id, papel="admin"))
    ctx.db.commit()
    flash(request, f"Workspace '{nome}' criado. Troque para ele no topo e cadastre perfis, departamentos e ofertas do novo setor.")
    return RedirectResponse("/operacao", status_code=303)


@router.post("/tarefas-presas/encerrar")
def encerrar_presas(request: Request, ctx: Ctx = Depends(admin_ctx)):
    ids = {j.id for j in health.stuck_jobs(ctx.db) if j.workspace_id == ctx.workspace_id}
    n = 0
    if ids:
        n = health.reap_stuck_jobs(ctx.db)
        ctx.db.commit()
    flash(request, f"{n} tarefa(s) presa(s) encerrada(s)." if n else "Nenhuma tarefa presa.")
    return RedirectResponse("/operacao", status_code=303)


__all__ = ["router", "HTTPException"]
