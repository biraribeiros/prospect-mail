"""Etapa 3: duplicados, contador, tipo de email, departamento e papéis."""
import pytest
from sqlalchemy import func, select

from app.core.models import (Company, Contact, ContactRole, Department, Evidence, OpportunitySignal)
from app.core.provenance import record_evidence
from app.hunter.resolution.classify import email_type, infer_role
from app.hunter.resolution.dedupe import find_pairs, merge_companies, undo_merge
from app.hunter.resolution.service import export_consolidated, run_resolution
from tests.conftest import token


def comp(db, ws, **kw):
    c = Company(workspace_id=ws.id, **kw)
    db.add(c)
    db.flush()
    return c


def test_mesma_empresa_via_cnpj_maps_e_site_vira_um_registro(db, world):
    ws = world.a
    comp(db, ws, cnpj="11222333000181", razao_social="ALFA CONSULTORIA LTDA", nome_fantasia="Alfa Consultoria",
         municipio="Campinas", uf="SP")
    comp(db, ws, nome_fantasia="Alfa Consultoria", dominio="alfa.com.br", site="https://alfa.com.br",
         telefone="(19) 3333-4444", municipio="Campinas", uf="SP")
    comp(db, ws, dominio="alfa.com.br", telefone="1933334444", razao_social=None)
    db.commit()
    res = run_resolution(db, ws.id)
    assert res["mescladas"] == 2
    vivos = db.scalars(select(Company).where(Company.workspace_id == ws.id, Company.merged_into_id.is_(None))).all()
    assert len(vivos) == 1
    v = vivos[0]
    assert v.cnpj == "11222333000181" and v.dominio == "alfa.com.br" and v.site == "https://alfa.com.br"
    assert v.telefone  # herdou dos absorvidos


def test_cnpjs_diferentes_nunca_mesclam(db, world):
    ws = world.a
    comp(db, ws, cnpj="11222333000181", nome_fantasia="Alfa", dominio="alfa.com.br", municipio="Campinas", uf="SP")
    comp(db, ws, cnpj="44555666000199", nome_fantasia="Alfa", dominio="alfa.com.br", municipio="Campinas", uf="SP")
    db.commit()
    assert find_pairs(db, ws.id) == []
    c1, c2 = db.scalars(select(Company)).all()
    with pytest.raises(ValueError):
        merge_companies(db, c1, c2, "teste")


def test_nome_parecido_na_mesma_cidade_mescla_mas_em_cidade_diferente_nao(db, world):
    ws = world.a
    a = comp(db, ws, nome_fantasia="Padaria Pão Quente", municipio="Campinas", uf="SP")
    b = comp(db, ws, nome_fantasia="Padaria Pao Quente", municipio="Campinas", uf="SP")
    comp(db, ws, nome_fantasia="Padaria Pão Quente", municipio="Santos", uf="SP")
    comp(db, ws, nome_fantasia="Padaria do Zé", municipio="Campinas", uf="SP")
    db.commit()
    pares = find_pairs(db, ws.id)
    assert [(x, y) for x, y, _ in pares] == [(a.id, b.id)] and pares[0][2] == "nome_cidade"


def test_dominio_generico_nao_une_empresas(db, world):
    ws = world.a
    comp(db, ws, nome_fantasia="Um", dominio="gmail.com")
    comp(db, ws, nome_fantasia="Dois", dominio="gmail.com")
    db.commit()
    assert find_pairs(db, ws.id) == []


def test_mesclagem_move_contatos_evidencias_e_sinais_e_pode_ser_desfeita(db, world):
    ws = world.a
    surv = comp(db, ws, cnpj="11222333000181", nome_fantasia="Alfa", dominio="alfa.com.br")
    lose = comp(db, ws, nome_fantasia="Alfa", dominio="alfa.com.br", telefone="1933334444")
    ct = Contact(workspace_id=ws.id, company_id=lose.id, email="a@alfa.com.br")
    db.add(ct)
    db.flush()
    record_evidence(db, workspace_id=ws.id, entity_type="company", entity_id=lose.id, field="telefone",
                    value="1933334444", source="google_places", url="https://maps.example/x", confidence=0.8)
    db.add_all([OpportunitySignal(workspace_id=ws.id, company_id=lose.id, tipo="vaga", titulo="v", fonte="f",
                                  url="https://x.example", confianca=0.5, hash="h1"),
                OpportunitySignal(workspace_id=ws.id, company_id=surv.id, tipo="vaga", titulo="v", fonte="f",
                                  url="https://x.example", confianca=0.5, hash="h1")])
    db.commit()
    rec = merge_companies(db, surv, lose, "dominio")
    db.commit()
    assert db.get(Contact, ct.id).company_id == surv.id
    assert db.scalar(select(func.count()).select_from(Evidence).where(Evidence.entidade_id == surv.id,
                                                                      Evidence.entidade_tipo == "company")) == 1
    assert db.scalar(select(func.count()).select_from(OpportunitySignal)) == 1  # sinal repetido não duplica
    assert db.get(Company, surv.id).telefone == "1933334444" and db.get(Company, lose.id).merged_into_id == surv.id
    undo_merge(db, rec.id, ws.id)
    db.commit()
    db.expire_all()
    assert db.get(Contact, ct.id).company_id == lose.id
    assert db.get(Company, lose.id).merged_into_id is None and db.get(Company, surv.id).telefone is None
    assert db.scalar(select(func.count()).select_from(Evidence).where(Evidence.entidade_id == lose.id,
                                                                      Evidence.entidade_tipo == "company")) == 1
    with pytest.raises(ValueError):
        undo_merge(db, rec.id, ws.id)


def test_cnpj_do_absorvido_vai_para_o_sobrevivente_e_volta_ao_desfazer(db, world):
    ws = world.a
    a = comp(db, ws, nome_fantasia="Alfa", dominio="alfa.com.br")
    b = comp(db, ws, cnpj="11222333000181", razao_social="ALFA LTDA", dominio="alfa.com.br")
    db.commit()
    rec = merge_companies(db, a, b, "dominio")  # força o sem-CNPJ como sobrevivente
    db.commit()
    assert db.get(Company, a.id).cnpj == "11222333000181" and db.get(Company, b.id).cnpj is None
    undo_merge(db, rec.id, ws.id)
    db.commit()
    db.expire_all()
    assert db.get(Company, a.id).cnpj is None and db.get(Company, b.id).cnpj == "11222333000181"


def test_mesclagem_entre_workspaces_e_barrada(db, world):
    a = comp(db, world.a, nome_fantasia="X")
    b = comp(db, world.b, nome_fantasia="X")
    db.commit()
    with pytest.raises(ValueError):
        merge_companies(db, a, b, "teste")


def test_contador_por_cnae_e_por_dominio_repetido(db, world):
    ws = world.a
    contab = comp(db, ws, nome_fantasia="Contábil Silva", cnae_principal="6920-6/01", dominio="silvacontabil.com.br")
    ct1 = Contact(workspace_id=ws.id, company_id=contab.id, email="oi@silvacontabil.com.br")
    db.add(ct1)
    for i in range(22):  # 22 empresas cujo email cadastral é do mesmo escritório
        c = comp(db, ws, cnpj=f"1000000{i:07d}", nome_fantasia=f"Cliente {i}")
        record_evidence(db, workspace_id=ws.id, entity_type="company", entity_id=c.id, field="email_cadastral",
                        value=f"cliente{i}@contabilidadeabc.com.br", source="cnpj_receita", confidence=0.3)
    outro = Contact(workspace_id=ws.id, email="fiscal@contabilidadeabc.com.br")
    normal = Contact(workspace_id=ws.id, email="maria@clienteok.com.br")
    db.add_all([outro, normal])
    db.commit()
    res = run_resolution(db, ws.id)
    db.expire_all()
    assert db.get(Company, contab.id).contador is True
    assert db.get(Contact, ct1.id).bloqueado_envio and "CNAE 69" in db.get(Contact, ct1.id).bloqueio_motivo
    assert db.get(Contact, outro.id).bloqueado_envio and "20 CNPJs" in db.get(Contact, outro.id).bloqueio_motivo
    assert not db.get(Contact, normal.id).bloqueado_envio
    assert res["contatos_bloqueados"] == 2


@pytest.mark.parametrize("email,tipo", [("maria.silva@x.com.br", "pessoal"), ("contato@x.com.br", "generico"),
                                        ("info@x.com.br", "generico"), ("comercial@x.com.br", "funcao"),
                                        ("rh@x.com.br", "funcao"), ("joao@x.com.br", "pessoal"),
                                        ("marketing.sp@x.com.br", "funcao")])
def test_tipo_do_email(email, tipo):
    assert email_type(email) == tipo


@pytest.mark.parametrize("cargo,papel", [("Gerente de Marketing", "aprovador"), ("Diretor Comercial", "decisor"),
                                         ("Sócio-Administrador", "decisor"), ("Analista de RH", "influenciador"),
                                         ("Assistente Administrativo", "usuario"), ("Gerente Financeiro", "orcamento"),
                                         (None, None), ("Astronauta", None)])
def test_papel_de_compra_pelo_cargo(cargo, papel):
    assert infer_role(cargo) == papel


def test_departamento_e_papel_ligados_ao_contato(db, world):
    ws = world.a
    c = comp(db, ws, nome_fantasia="Alfa")
    por_cargo = Contact(workspace_id=ws.id, company_id=c.id, email="ana@alfa.com", cargo="Gerente de Marketing")
    por_email = Contact(workspace_id=ws.id, company_id=c.id, email="rh@alfa.com")
    db.add_all([por_cargo, por_email])
    db.commit()
    run_resolution(db, ws.id)
    db.expire_all()
    mkt = db.scalar(select(Department.id).where(Department.workspace_id == ws.id, Department.nome == "Marketing e Comunicação"))
    rh = db.scalar(select(Department.id).where(Department.workspace_id == ws.id, Department.nome == "RH e Gente"))
    assert db.get(Contact, por_cargo.id).department_id == mkt and db.get(Contact, por_email.id).department_id == rh
    papel = db.scalar(select(ContactRole).where(ContactRole.contact_id == por_cargo.id))
    assert papel.papel == "aprovador" and float(papel.confianca) == 0.6
    run_resolution(db, ws.id)  # de novo: sem duplicar papéis
    assert db.scalar(select(func.count()).select_from(ContactRole).where(ContactRole.contact_id == por_cargo.id)) == 1


def test_exportacao_consolidada_protege_contra_formulas(db, world):
    ws = world.a
    c = comp(db, ws, nome_fantasia='=HYPERLINK("http://x")', cnpj="11222333000181")
    db.add(Contact(workspace_id=ws.id, company_id=c.id, email="a@b.com", nome_completo="@maria"))
    db.commit()
    csv_txt = export_consolidated(db, ws.id)
    assert csv_txt.splitlines()[0].startswith("empresa,cnpj")
    assert "'=HYPERLINK" in csv_txt and "'@maria" in csv_txt


def test_telas_e_isolamento(logged, db, world):
    comp(db, world.b, nome_fantasia="Empresa Secreta B")
    a = comp(db, world.a, nome_fantasia="Alfa", dominio="alfa.com.br")
    b = comp(db, world.a, nome_fantasia="Alfa Matriz", dominio="alfa.com.br")
    db.commit()
    rec = merge_companies(db, a, b, "dominio")
    db.commit()
    html = logged.get("/hunter/duplicados").text
    assert "Desfazer" in html and "Empresa Secreta B" not in html
    csv_resp = logged.get("/hunter/exportar/consolidada.csv")
    assert csv_resp.status_code == 200 and "Empresa Secreta B" not in csv_resp.text
    assert "Alfa Matriz" not in logged.get("/hunter/empresas").text  # absorvida não aparece na lista
    r = logged.post(f"/hunter/duplicados/{rec.id}/desfazer", data={"csrf_token": token(logged, "/hunter/duplicados")})
    assert r.status_code == 303
    assert "Alfa Matriz" in logged.get("/hunter/empresas").text
