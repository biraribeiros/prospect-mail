from dataclasses import dataclass
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.models import AppUser, Workspace, WorkspaceMember
from app.core.security import csrf_matches, new_csrf_token

WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))


class LoginRequired(Exception):
    pass


class WorkspaceRequired(Exception):
    pass


def ensure_csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = new_csrf_token()
        request.session["csrf"] = token
    return token


def pop_flash(request: Request) -> list[dict]:
    return request.session.pop("flash", [])


def flash(request: Request, message: str, kind: str = "ok") -> None:
    request.session.setdefault("flash", []).append({"msg": message, "kind": kind})


def safe_url(value: str | None) -> str:
    """Só links http(s): evita 'javascript:' vindo de dados importados ou de sites de terceiros."""
    v = (value or "").strip()
    return v if v.lower().startswith(("http://", "https://")) else "#"


def cnpj_fmt(value: str | None) -> str:
    v = value or ""
    return f"{v[:2]}.{v[2:5]}.{v[5:8]}/{v[8:12]}-{v[12:]}" if len(v) == 14 and v.isdigit() else v


templates.env.globals["csrf"] = ensure_csrf
templates.env.globals["pop_flash"] = pop_flash
templates.env.filters["safe_url"] = safe_url
templates.env.filters["cnpj_fmt"] = cnpj_fmt


async def csrf_guard(request: Request) -> None:
    """Protege todo POST/PUT/PATCH/DELETE: o token da sessão precisa voltar no formulário ou no cabeçalho."""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    provided = request.headers.get("x-csrf-token")
    if not provided:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form = await request.form()
            provided = form.get("csrf_token")  # type: ignore[assignment]
    if not csrf_matches(request.session.get("csrf"), provided):
        raise HTTPException(status_code=403, detail="Sessão expirada ou token inválido. Recarregue a página.")


def current_user(request: Request, db: Session = Depends(get_db)) -> AppUser:
    uid = request.session.get("uid")
    user = db.get(AppUser, uid) if uid else None
    if user is None or not user.ativo:
        raise LoginRequired()
    return user


@dataclass
class Ctx:
    user: AppUser
    workspace: Workspace
    papel: str
    db: Session

    @property
    def workspace_id(self) -> int:
        return self.workspace.id

    @property
    def can_edit(self) -> bool:
        return self.papel in ("admin", "operador")


def current_ctx(
    request: Request, user: AppUser = Depends(current_user), db: Session = Depends(get_db)
) -> Ctx:
    wid = request.session.get("wid")
    member = (
        db.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.user_id == user.id, WorkspaceMember.workspace_id == wid
            )
        )
        if wid
        else None
    )
    if member is None:
        raise WorkspaceRequired()
    return Ctx(user=user, workspace=db.get(Workspace, wid), papel=member.papel, db=db)


def editor_ctx(ctx: Ctx = Depends(current_ctx)) -> Ctx:
    if not ctx.can_edit:
        raise HTTPException(status_code=403, detail="Seu papel não permite alterar cadastros.")
    return ctx


def render(request: Request, name: str, ctx: Ctx | None = None, status: int = 200, **kwargs):
    return templates.TemplateResponse(request, name, {"ctx": ctx, **kwargs}, status_code=status)
