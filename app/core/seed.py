"""Semente idempotente: workspace 'Agência VIA', departamentos e (se configurado) usuário admin.

Executada a cada subida do container: rodar de novo não duplica nada.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.models import AppUser, Department, Workspace, WorkspaceMember
from app.core.security import hash_password
from app.core.seed_data import DEPARTAMENTOS, necessidades_json

WORKSPACE_VIA = "Agência VIA"


def seed_workspace_via(session: Session) -> Workspace:
    ws = session.scalar(select(Workspace).where(Workspace.nome == WORKSPACE_VIA))
    if ws is None:
        ws = Workspace(nome=WORKSPACE_VIA, config={"setor": "agencia_comunicacao"}, limites={})
        session.add(ws)
        session.flush()
    existentes = {d.nome for d in session.scalars(select(Department).where(Department.workspace_id == ws.id))}
    for d in DEPARTAMENTOS:
        if d["nome"] in existentes:
            continue
        session.add(
            Department(
                workspace_id=ws.id,
                nome=d["nome"],
                email_patterns=d["email_patterns"],
                cargos=d["cargos"],
                necessidades_previstas=necessidades_json(d["necessidades"]),
            )
        )
    session.flush()
    return ws


def seed_admin(session: Session, ws: Workspace, email: str, password: str) -> AppUser:
    email = email.strip().lower()
    user = session.scalar(select(AppUser).where(AppUser.email == email))
    if user is None:
        user = AppUser(email=email, nome="Administrador", senha_hash=hash_password(password))
        session.add(user)
        session.flush()
    member = session.scalar(
        select(WorkspaceMember).where(WorkspaceMember.user_id == user.id, WorkspaceMember.workspace_id == ws.id)
    )
    if member is None:
        session.add(WorkspaceMember(user_id=user.id, workspace_id=ws.id, papel="admin"))
        session.flush()
    return user


def run() -> None:
    settings = get_settings()
    settings.check_production_safety()
    with SessionLocal() as session:
        ws = seed_workspace_via(session)
        if settings.admin_email and settings.admin_password:
            seed_admin(session, ws, settings.admin_email, settings.admin_password)
        session.commit()
    print("Semente aplicada.")


if __name__ == "__main__":
    run()
