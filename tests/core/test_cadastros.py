from sqlalchemy import select

from app.core.models import Department, IcpProfile
from app.web.forms import norm_cnae, parse_department, parse_necessidades, split_list
from tests.conftest import token


# ---------- funções de leitura de formulário ----------
def test_split_list():
    assert split_list("a, b\nc;  a ,,") == ["a", "b", "c"]
    assert split_list(None) == []


def test_norm_cnae():
    assert norm_cnae("7020-4") == "70.20-4" and norm_cnae("70.20-4") == "70.20-4"
    assert norm_cnae("7020-4/00") == "7020-4/00" and norm_cnae("70") == "70"
    assert norm_cnae("abc") is None and norm_cnae("123456") is None


def test_parse_necessidades():
    itens, erros = parse_necessidades("Vídeo | 5 | video, social\nSó nome\n\nRuim | 9 | x\n")
    assert itens[0] == {"necessidade": "Vídeo", "relevancia": 5, "ofertas_relacionadas": ["video", "social"]}
    assert itens[1]["relevancia"] == 3  # padrão
    assert len(erros) == 1 and "Linha 4" in erros[0]


def test_parse_department_normaliza_padroes():
    data, erros = parse_department("RH", "RH, recrutamento@, inválido!", "Gerente", "")
    assert data["email_patterns"] == ["rh@", "recrutamento@"] and len(erros) == 1


# ---------- ICP ----------
def test_cria_edita_exclui_icp(logged, world, db):
    tok = token(logged, "/configuracoes/icp/novo")
    r = logged.post("/configuracoes/icp", data={
        "nome": "Contabilidade MG", "cnaes": "6920-6\n70.20-4", "ufs": "mg, sp", "portes": "me, epp",
        "municipios": "Patos de Minas", "termos_maps": "contador", "departamentos_alvo": ["Diretoria", "TI"],
        "csrf_token": tok})
    assert r.status_code == 303
    icp = db.scalar(select(IcpProfile).where(IcpProfile.nome == "Contabilidade MG"))
    icp_id = icp.id
    assert icp.workspace_id == world.a.id
    assert icp.cnaes == ["69.20-6", "70.20-4"] and icp.ufs == ["MG", "SP"] and icp.portes == ["ME", "EPP"]
    assert icp.departamentos_alvo == ["Diretoria", "TI"]
    assert "Contabilidade MG" in logged.get("/configuracoes/icp").text

    r = logged.post(f"/configuracoes/icp/{icp_id}", data={"nome": "Contab. renomeado", "ufs": "MG", "csrf_token": tok})
    assert r.status_code == 303
    db.expire_all()
    assert db.get(IcpProfile, icp_id).nome == "Contab. renomeado" and db.get(IcpProfile, icp_id).cnaes == []

    r = logged.post(f"/configuracoes/icp/{icp_id}/excluir", data={"csrf_token": tok}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and r.text == ""
    db.expire_all()
    assert db.get(IcpProfile, icp_id) is None


def test_icp_invalido_nao_grava_e_mostra_erros(logged, db):
    tok = token(logged, "/configuracoes/icp/novo")
    r = logged.post("/configuracoes/icp", data={"nome": "", "ufs": "XX", "portes": "GIGANTE", "cnaes": "abc",
                                                 "csrf_token": tok})
    assert r.status_code == 422
    for msg in ["Dê um nome", "UF inválida", "Porte inválido", "CNAE inválido"]:
        assert msg in r.text
    assert db.scalar(select(IcpProfile)) is None


# ---------- Departamentos ----------
def test_cria_edita_exclui_departamento(logged, world, db):
    tok = token(logged, "/configuracoes/departamentos/novo")
    r = logged.post("/configuracoes/departamentos", data={
        "nome": "Logística", "email_patterns": "logistica, frete@", "cargos": "Gerente de Logística",
        "necessidades": "Vídeo de processos | 4 | video", "csrf_token": tok})
    assert r.status_code == 303
    d = db.scalar(select(Department).where(Department.nome == "Logística"))
    dept_id = d.id
    assert d.workspace_id == world.a.id and d.email_patterns == ["logistica@", "frete@"]
    assert d.necessidades_previstas == [{"necessidade": "Vídeo de processos", "relevancia": 4, "ofertas_relacionadas": ["video"]}]
    assert "Vídeo de processos | 4 | video" in logged.get(f"/configuracoes/departamentos/{dept_id}").text

    r = logged.post(f"/configuracoes/departamentos/{dept_id}", data={"nome": "Logística e Frota", "csrf_token": tok})
    assert r.status_code == 303
    db.expire_all()
    assert db.get(Department, dept_id).nome == "Logística e Frota"

    assert logged.post(f"/configuracoes/departamentos/{dept_id}/excluir", data={"csrf_token": tok}).status_code == 303
    db.expire_all()
    assert db.get(Department, dept_id) is None


def test_departamento_nome_repetido_e_necessidade_invalida(logged):
    tok = token(logged, "/configuracoes/departamentos/novo")
    r = logged.post("/configuracoes/departamentos", data={"nome": "TI", "csrf_token": tok})
    assert r.status_code == 422 and "Já existe" in r.text
    r = logged.post("/configuracoes/departamentos", data={"nome": "Novo", "necessidades": "x | 9", "csrf_token": tok})
    assert r.status_code == 422 and "relevância" in r.text
