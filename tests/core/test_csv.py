from sqlalchemy import func, select

from app.core.csvio import cnpj_valido, norm_domain, safe_cell
from app.core.models import ComplianceRecord, Company, Contact, Evidence, JobRun
from tests.conftest import token

CNPJ_OK = "11222333000181"


def contatos_csv(n=100, extra="", sep=","):
    linhas = ["nome_completo", "email", "cargo", "departamento", "cnpj", "dominio"]
    out = [sep.join(linhas)]
    for i in range(n):
        out.append(sep.join([f"Pessoa Numero{i}", f"pessoa{i}@empresa{i % 20}.com.br", "Gerente",
                             "Marketing e Comunicação", "", f"empresa{i % 20}.com.br"]))
    return ("\n".join(out) + "\n" + extra).encode("utf-8")


def enviar(client, tipo, conteudo, nome="lista.csv"):
    tok = token(client, "/configuracoes/importar")
    return client.post(f"/configuracoes/importar/{tipo}", data={"csrf_token": tok},
                       files={"file": (nome, conteudo, "text/csv")})


# ---------- critério de aceite: 100 contatos ----------
def test_importa_100_contatos_com_evidencia_e_conformidade(logged, world, db):
    r = enviar(logged, "contatos", contatos_csv(100))
    assert r.status_code == 200 and "100" in r.text
    ws = world.a.id
    assert db.scalar(select(func.count()).select_from(Contact).where(Contact.workspace_id == ws)) == 100
    # evidência de origem "importacao_manual" para cada contato (campo email) com URL e confiança
    evs = db.scalars(select(Evidence).where(Evidence.campo == "email")).all()
    assert len(evs) == 100 and {e.fonte for e in evs} == {"importacao_manual"}
    assert all(e.url == "csv://lista.csv" and 0 < float(e.confianca) <= 1 and e.coletado_em for e in evs)
    # um registro de conformidade por contato
    comp = db.scalars(select(ComplianceRecord).where(ComplianceRecord.entidade_tipo == "contact")).all()
    assert len(comp) == 100 and {c.origem_dado for c in comp} == {"importacao_manual"}
    assert all(c.finalidade and c.base_legal and c.retencao_ate for c in comp)
    c0 = db.scalar(select(Contact).where(Contact.email == "pessoa0@empresa0.com.br"))
    assert c0.primeiro_nome == "Pessoa" and c0.origem == "importacao_manual" and c0.department_id is not None
    job = db.scalar(select(JobRun).where(JobRun.tipo == "import_contatos"))
    assert job.status == "concluido" and job.contadores["criados"] == 100


def test_reimportar_mesmo_arquivo_nao_duplica(logged, world, db):
    enviar(logged, "contatos", contatos_csv(100))
    r = enviar(logged, "contatos", contatos_csv(100))
    assert "já foi importado" in r.text
    assert db.scalar(select(func.count()).select_from(Contact)) == 100
    assert db.scalar(select(func.count()).select_from(JobRun)) == 1


def test_arquivo_alterado_faz_upsert_sem_duplicar(logged, world, db):
    enviar(logged, "contatos", contatos_csv(100))
    novo = contatos_csv(100, extra="Nova Pessoa,nova@empresa1.com.br,Diretor,Diretoria,,\n").replace(
        b"Pessoa Numero5,pessoa5@empresa5.com.br,Gerente", b"Pessoa Numero5,pessoa5@empresa5.com.br,Diretor")
    r = enviar(logged, "contatos", novo, nome="lista_v2.csv")
    assert r.status_code == 200
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Contact)) == 101
    assert db.scalar(select(Contact.cargo).where(Contact.email == "pessoa5@empresa5.com.br")) == "Diretor"
    # evidência de email não se repete: 100 antigos + 1 novo
    assert db.scalar(select(func.count()).select_from(Evidence).where(Evidence.campo == "email")) == 101
    # o cargo alterado ganhou uma evidência nova, sem apagar a anterior (histórico)
    cargos = db.scalars(select(Evidence.valor).where(Evidence.campo == "cargo", Evidence.entidade_tipo == "contact",
                                                    Evidence.entidade_id == db.scalar(select(Contact.id).where(Contact.email == "pessoa5@empresa5.com.br")))).all()
    assert sorted(cargos) == ["Diretor", "Gerente"]


def test_linhas_invalidas_sao_reportadas_e_nao_importadas(logged, db):
    csv = ("nome,email,cargo,departamento\n"
           "Ok,ok@empresa.com.br,Gerente,TI\n"
           "Sem arroba,sem-arroba,Gerente,TI\n"
           "Depto errado,x@empresa.com.br,Gerente,Departamento Fantasma\n").encode()
    r = enviar(logged, "contatos", csv)
    assert "Email inválido" in r.text and "Departamento desconhecido" in r.text
    assert [c.email for c in db.scalars(select(Contact))] == ["ok@empresa.com.br"]


def test_aceita_ponto_e_virgula_e_latin1(logged, db):
    csv = "Nome;E-mail;Cargo;Setor\nJosé Ávila;jose@empresa.com.br;Diretor;Diretoria\n".encode("latin-1")
    enviar(logged, "contatos", csv)
    c = db.scalar(select(Contact))
    assert c.nome_completo == "José Ávila" and c.cargo == "Diretor" and c.department_id is not None


def test_arquivo_vazio_ou_grande_demais(logged):
    assert enviar(logged, "contatos", b"").status_code == 422
    assert enviar(logged, "contatos", b"a" * (5 * 1024 * 1024 + 10)).status_code == 422


# ---------- empresas ----------
def test_importa_empresas_valida_cnpj_uf_porte(logged, world, db):
    csv = (f"cnpj,razao_social,dominio,uf,porte\n"
           f"{CNPJ_OK},Empresa Boa Ltda,https://www.Boa.com.br/,MG,ME\n"
           f"11222333000182,CNPJ errado,x.com.br,MG,ME\n"
           f",UF ruim,y.com.br,ZZ,ME\n"
           f",Porte ruim,z.com.br,MG,GIGANTE\n").encode()
    r = enviar(logged, "empresas", csv)
    assert r.status_code == 200 and "CNPJ inválido" in r.text and "UF inválida" in r.text and "Porte inválido" in r.text
    emp = db.scalars(select(Company)).all()
    assert len(emp) == 1 and emp[0].cnpj == CNPJ_OK and emp[0].dominio == "boa.com.br"
    assert db.scalar(select(func.count()).select_from(ComplianceRecord).where(ComplianceRecord.entidade_tipo == "company")) == 1
    assert db.scalar(select(Evidence.fonte).where(Evidence.entidade_tipo == "company", Evidence.campo == "cnpj")) == "importacao_manual"


def test_contato_e_vinculado_a_empresa_pelo_dominio(logged, db):
    enviar(logged, "empresas", b"razao_social,dominio\nBoa Ltda,boa.com.br\n")
    enviar(logged, "contatos", b"nome,email,dominio\nAna,ana@boa.com.br,boa.com.br\n")
    c, emp = db.scalar(select(Contact)), db.scalar(select(Company))
    assert c.company_id == emp.id


def test_empresa_nao_duplica_por_cnpj(logged, db):
    enviar(logged, "empresas", f"cnpj,razao_social\n{CNPJ_OK},Antiga\n".encode())
    enviar(logged, "empresas", f"cnpj,razao_social\n{CNPJ_OK},Nova Razão\n".encode())
    emp = db.scalars(select(Company)).all()
    assert len(emp) == 1 and emp[0].razao_social == "Nova Razão"


# ---------- exportação ----------
def test_exportacao_contatos_e_roundtrip(logged, db):
    enviar(logged, "contatos", contatos_csv(3))
    r = logged.get("/exportar/contatos.csv")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    linhas = r.text.lstrip("﻿").strip().splitlines()
    assert linhas[0] == "nome_completo,email,cargo,departamento,cnpj,dominio" and len(linhas) == 4


def test_exportacao_protege_contra_formula_do_excel(logged, db, world):
    db.add(Contact(workspace_id=world.a.id, email="x@y.com.br", nome_completo='=HYPERLINK("http://mal.com")'))
    db.commit()
    assert "'=HYPERLINK" in logged.get("/exportar/contatos.csv").text
    assert safe_cell("+55 34 9999") == "'+55 34 9999" and safe_cell("normal") == "normal" and safe_cell(None) == ""


def test_modelos_de_planilha(logged):
    assert logged.get("/configuracoes/modelo/contatos.csv").text.lstrip("﻿").startswith("nome_completo,email")
    assert logged.get("/configuracoes/modelo/empresas.csv").text.lstrip("﻿").startswith("cnpj,razao_social")


def test_helpers():
    assert cnpj_valido(CNPJ_OK) and not cnpj_valido("11222333000182") and not cnpj_valido("00000000000000")
    assert norm_domain("HTTPS://www.Exemplo.com.br/pagina") == "exemplo.com.br" and norm_domain("") is None
