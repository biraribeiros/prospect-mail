import os
import re
from urllib.parse import urlsplit, urlunsplit

# --- Configuração de teste: banco separado e chaves descartáveis (antes de importar o app) ---
_base = os.environ.get("DATABASE_URL", "postgresql+psycopg://prospector:prospector@localhost:5432/prospector")
_parts = urlsplit(_base)
TEST_DB = "prospector_test"
TEST_URL = urlunsplit(_parts._replace(path=f"/{TEST_DB}"))
ADMIN_URL = _base
os.environ["DATABASE_URL"] = TEST_URL
os.environ["SECRET_KEY"] = "chave-de-teste-nao-usar-em-producao-0123456789"
os.environ["FERNET_KEYS"] = "YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXowMTIzNDU="
os.environ["APP_ENV"] = "dev"
os.environ["DRY_RUN"] = "true"
os.environ["ADMIN_EMAIL"] = ""
os.environ["ADMIN_PASSWORD"] = ""

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.core.models import AppUser, Base, Workspace, WorkspaceMember  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.core.seed import seed_workspace_via  # noqa: E402
from app.main import app  # noqa: E402
from app.web.routes import auth as auth_routes  # noqa: E402

SENHA = "senha-de-teste-123"


@pytest.fixture(scope="session", autouse=True)
def _database():
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        if not c.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_DB}):
            c.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    admin.dispose()
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def _clean():
    auth_routes._FALHAS.clear()
    yield
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with SessionLocal() as s:
        s.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        s.commit()


@pytest.fixture
def db():
    with SessionLocal() as s:
        yield s


class World:
    pass


@pytest.fixture
def world(db):
    """Dois workspaces isolados (A = Agência VIA com departamentos, B = outro setor) e 4 usuários."""
    w = World()
    w.a = seed_workspace_via(db)
    w.b = Workspace(nome="Clínica Saúde")
    db.add(w.b)
    db.flush()

    def user(email, ws, papel):
        u = db.query(AppUser).filter_by(email=email).first() or AppUser(
            email=email, nome=email, senha_hash=hash_password(SENHA)
        )
        db.add(u)
        db.flush()
        db.add(WorkspaceMember(user_id=u.id, workspace_id=ws.id, papel=papel))
        db.flush()
        return u

    w.user_a = user("a@teste.com", w.a, "admin")
    w.user_b = user("b@teste.com", w.b, "admin")
    w.approver_a = user("aprovador@teste.com", w.a, "aprovador")
    w.operador_a = user("operador@teste.com", w.a, "operador")
    db.commit()
    return w


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


def token(client, path="/") -> str:
    html = client.get(path).text
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, f"sem token CSRF em {path}"
    return m.group(1)


def login(client, email, senha=SENHA):
    tok = token(client, "/login")
    return client.post("/login", data={"email": email, "senha": senha, "csrf_token": tok})


@pytest.fixture
def logged(client, world):
    login(client, world.user_a.email)
    return client
