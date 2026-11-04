import time

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.models import AppUser, Workspace, WorkspaceMember
from app.core.security import verify_password
from app.web.deps import current_user, ensure_csrf, render

router = APIRouter()

# Limite simples de tentativas (em memória): 10 falhas por IP+email a cada 15 min.
_FALHAS: dict[str, list[float]] = {}
JANELA, MAX_FALHAS = 15 * 60, 10


def _chave(request: Request, email: str) -> str:
    ip = request.client.host if request.client else "?"
    return f"{ip}|{email}"


def _bloqueado(chave: str) -> bool:
    agora = time.time()
    recentes = [t for t in _FALHAS.get(chave, []) if agora - t < JANELA]
    _FALHAS[chave] = recentes
    return len(recentes) >= MAX_FALHAS


@router.get("/login")
def login_page(request: Request):
    ensure_csrf(request)
    return render(request, "login.html")


@router.post("/login")
def login(request: Request, email: str = Form(""), senha: str = Form(""), db: Session = Depends(get_db)):
    email = email.strip().lower()
    chave = _chave(request, email)
    if _bloqueado(chave):
        return render(request, "login.html", status=429, error="Muitas tentativas. Aguarde 15 minutos.")
    user = db.scalar(select(AppUser).where(AppUser.email == email))
    ok = verify_password(senha, user.senha_hash if user else None)
    if not (ok and user and user.ativo):
        _FALHAS.setdefault(chave, []).append(time.time())
        return render(request, "login.html", status=400, error="Email ou senha incorretos.", email=email)
    _FALHAS.pop(chave, None)
    request.session.clear()  # evita fixação de sessão; um novo token CSRF é criado a seguir
    request.session["uid"] = user.id
    ensure_csrf(request)
    workspaces = db.scalars(select(WorkspaceMember).where(WorkspaceMember.user_id == user.id)).all()
    if len(workspaces) == 1:
        request.session["wid"] = workspaces[0].workspace_id
        return RedirectResponse("/", status_code=303)
    return RedirectResponse("/workspaces", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/workspaces")
def workspaces_page(request: Request, user: AppUser = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Workspace, WorkspaceMember.papel)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(Workspace.nome)
    ).all()
    return render(request, "workspaces.html", user=user, workspaces=rows)


@router.post("/workspaces")
def select_workspace(
    request: Request,
    workspace_id: int = Form(...),
    user: AppUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    member = db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.user_id == user.id, WorkspaceMember.workspace_id == workspace_id
        )
    )
    if member is None:
        raise HTTPException(status_code=403, detail="Você não tem acesso a este workspace.")
    request.session["wid"] = workspace_id
    return RedirectResponse("/", status_code=303)
