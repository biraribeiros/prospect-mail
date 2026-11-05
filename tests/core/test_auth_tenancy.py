from sqlalchemy import select

from app.core.models import Company, Contact, IcpProfile
from tests.conftest import SENHA, login, token


# ---------- autenticação ----------
def test_login_ok_cookie_seguro_e_redireciona(client, world):
    r = login(client, "a@teste.com")
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/").status_code == 200


def test_senha_guardada_com_argon2(db, world):
    assert world.user_a.senha_hash.startswith("$argon2") and SENHA not in world.user_a.senha_hash


def test_login_erro_nao_revela_se_email_existe(client, world):
    r1 = login(client, "a@teste.com", "errada")
    r2 = login(client, "naoexiste@teste.com", "errada")
    assert r1.status_code == r2.status_code == 400
    assert "Email ou senha incorretos" in r1.text and "Email ou senha incorretos" in r2.text


def test_rotas_protegidas_redirecionam_para_login(client, world):
    for path in ["/", "/hunter", "/configuracoes", "/configuracoes/icp", "/exportar/contatos.csv"]:
        r = client.get(path)
        assert r.status_code == 303 and r.headers["location"] == "/login", path


def test_post_sem_csrf_e_barrado(client, world):
    login(client, "a@teste.com")
    assert client.post("/logout").status_code == 403
    assert client.post("/configuracoes/icp", data={"nome": "X"}).status_code == 403
    assert client.post("/logout", data={"csrf_token": "errado"}).status_code == 403
    assert client.get("/").status_code == 200  # continua logado


def test_logout_encerra_sessao(client, world):
    login(client, "a@teste.com")
    assert client.post("/logout", data={"csrf_token": token(client)}).status_code == 303
    assert client.get("/").status_code == 303


def test_bloqueio_apos_muitas_tentativas(client, world):
    for _ in range(10):
        assert login(client, "a@teste.com", "errada").status_code == 400
    assert login(client, "a@teste.com", "errada").status_code == 429
    assert login(client, "a@teste.com").status_code == 429  # mesmo com senha certa, durante o bloqueio


def test_usuario_com_dois_workspaces_escolhe(client, world, db):
    from app.core.models import WorkspaceMember

    db.add(WorkspaceMember(user_id=world.user_a.id, workspace_id=world.b.id, papel="operador"))
    db.commit()
    r = login(client, "a@teste.com")
    assert r.headers["location"] == "/workspaces"
    assert client.get("/").headers["location"] == "/workspaces"  # sem workspace escolhido
    r = client.post("/workspaces", data={"workspace_id": world.b.id, "csrf_token": token(client, "/workspaces")})
    assert r.status_code == 303 and client.get("/").status_code == 200


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "dry_run": True}


def test_secoes_principais_abrem(logged):
    for slug, titulo in [("filtro", "Filtro"), ("inteligencia", "Inteligência"), ("disparo", "Disparo"), ("metricas", "Métricas")]:
        r = logged.get(f"/{slug}")
        assert r.status_code == 200 and titulo in r.text and "Em construção" not in r.text


# ---------- isolamento entre workspaces ----------
def test_nao_seleciona_workspace_de_outro(client, world):
    login(client, "a@teste.com")
    r = client.post("/workspaces", data={"workspace_id": world.b.id, "csrf_token": token(client)})
    assert r.status_code == 403


def test_workspace_a_nao_ve_nem_altera_icp_do_b(logged, world, db):
    icp_b = IcpProfile(workspace_id=world.b.id, nome="Segredo do B", ufs=["SP"])
    db.add(icp_b)
    db.commit()
    assert "Segredo do B" not in logged.get("/configuracoes/icp").text
    tok = token(logged, "/configuracoes/icp/novo")
    assert logged.get(f"/configuracoes/icp/{icp_b.id}").status_code == 404
    assert logged.post(f"/configuracoes/icp/{icp_b.id}", data={"nome": "Hack", "csrf_token": tok}).status_code == 404
    assert logged.post(f"/configuracoes/icp/{icp_b.id}/excluir", data={"csrf_token": tok}).status_code == 404
    db.expire_all()
    assert db.get(IcpProfile, icp_b.id).nome == "Segredo do B"


def test_workspace_a_nao_acessa_departamento_do_b(logged, world, db):
    from app.core.models import Department

    d = Department(workspace_id=world.b.id, nome="Depto do B")
    db.add(d)
    db.commit()
    tok = token(logged, "/configuracoes/departamentos/novo")
    assert "Depto do B" not in logged.get("/configuracoes/departamentos").text
    assert logged.get(f"/configuracoes/departamentos/{d.id}").status_code == 404
    assert logged.post(f"/configuracoes/departamentos/{d.id}/excluir", data={"csrf_token": tok}).status_code == 404


def test_exportacao_so_traz_dados_do_proprio_workspace(logged, world, db):
    db.add_all([
        Contact(workspace_id=world.a.id, email="a@empresa-a.com.br", nome_completo="Pessoa A"),
        Contact(workspace_id=world.b.id, email="b@empresa-b.com.br", nome_completo="Pessoa B"),
        Company(workspace_id=world.b.id, razao_social="Empresa B Ltda"),
    ])
    db.commit()
    csv_contatos = logged.get("/exportar/contatos.csv").text
    assert "a@empresa-a.com.br" in csv_contatos and "b@empresa-b.com.br" not in csv_contatos
    assert "Empresa B" not in logged.get("/exportar/empresas.csv").text


def test_aprovador_ve_mas_nao_altera_cadastros(client, world):
    login(client, "aprovador@teste.com")
    assert client.get("/configuracoes/icp").status_code == 200
    assert client.get("/configuracoes/icp/novo").status_code == 403
    tok = token(client, "/configuracoes/icp")
    assert client.post("/configuracoes/icp", data={"nome": "X", "csrf_token": tok}).status_code == 403
    r = client.post("/configuracoes/importar/contatos", data={"csrf_token": tok},
                    files={"file": ("c.csv", b"email\nx@y.com.br\n", "text/csv")})
    assert r.status_code == 403


def test_operador_pode_criar_icp(client, world, db):
    login(client, "operador@teste.com")
    tok = token(client, "/configuracoes/icp/novo")
    r = client.post("/configuracoes/icp", data={"nome": "Do operador", "csrf_token": tok})
    assert r.status_code == 303
    assert db.scalar(select(IcpProfile).where(IcpProfile.nome == "Do operador")).workspace_id == world.a.id
