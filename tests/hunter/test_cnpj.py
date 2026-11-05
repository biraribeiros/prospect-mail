"""Importador da base CNPJ: filtra pelo perfil, grava evidência/conformidade e é idempotente."""
import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.csvio import cnpj_valido
from app.core.models import (Company, ComplianceRecord, Contact, ContactRole, Evidence, IcpProfile, JobRun)
from app.hunter.companies import cnpj as cnpj_mod
from app.hunter.companies.service import run_cnpj
from tests.hunter.receita import cnpj_de, write_set


def icp(db, ws, **kw):
    base = dict(nome="Consultoria SP", cnaes=["70.20-4"], ufs=["SP"], portes=["ME", "EPP"])
    base.update(kw)
    i = IcpProfile(workspace_id=ws.id, **base)
    db.add(i)
    db.commit()
    return i


def n(db, model, *where):
    return db.scalar(select(func.count()).select_from(model).where(*where))


def test_cnpjs_de_teste_sao_validos():
    assert cnpj_valido(cnpj_de("11111111")) and cnpj_valido(cnpj_de("77777777"))


@pytest.mark.parametrize("zipped", [False, True])
def test_importa_so_o_que_esta_no_perfil(db, world, tmp_path, zipped):
    write_set(tmp_path, zipped=zipped)
    res = cnpj_mod.import_cnpj(db, world.a.id, icp(db, world.a), tmp_path, cap=1000)
    db.commit()
    assert res["encontrados"] == 3 and res["empresas_no_perfil"] == 2  # o de porte DEMAIS cai no filtro de porte
    assert res["criadas"] == 2 and res["socios"] == 3 and res["cnpj_invalidos"] == 0
    cnpjs = set(db.scalars(select(Company.cnpj)))
    assert cnpjs == {cnpj_de("11111111"), cnpj_de("77777777")}
    c = db.scalar(select(Company).where(Company.cnpj == cnpj_de("11111111")))
    assert (c.razao_social, c.nome_fantasia, c.uf, c.porte, c.situacao_cadastral) == (
        "ALFA CONSULTORIA LTDA", "ALFA CONSULTORIA", "SP", "ME", "ativa")
    assert c.cnae_principal == "7020-4/00" and c.cnae_secundarios == ["6202-3/00", "7490-1/04"]
    assert c.municipio == "Sao Paulo" and c.data_abertura.isoformat() == "2015-03-14"
    assert c.telefone == "1135551234" and "RUA DAS FLORES" in c.endereco and c.workspace_id == world.a.id


def test_socios_viram_contatos_com_papel(db, world, tmp_path):
    write_set(tmp_path)
    cnpj_mod.import_cnpj(db, world.a.id, icp(db, world.a), tmp_path, cap=1000)
    db.commit()
    nomes = {c.nome_completo: c for c in db.scalars(select(Contact))}
    assert set(nomes) == {"Maria da Silva", "Jose Santos", "Ana Lima"}  # PJ e sócio de empresa fora do perfil ficam de fora
    assert nomes["Maria da Silva"].cargo == "Sócio-Administrador" and nomes["Jose Santos"].cargo == "Sócio"
    assert nomes["Maria da Silva"].email is None and nomes["Maria da Silva"].origem == "cnpj_receita"
    papeis = {db.get(Contact, r.contact_id).nome_completo: r.papel for r in db.scalars(select(ContactRole))}
    assert papeis == {"Maria da Silva": "administrador", "Jose Santos": "socio", "Ana Lima": "administrador"}


def test_evidencia_em_cada_campo_e_conformidade(db, world, tmp_path):
    write_set(tmp_path)
    cnpj_mod.import_cnpj(db, world.a.id, icp(db, world.a), tmp_path, cap=1000)
    db.commit()
    for c in db.scalars(select(Company)):
        ev = {e.campo: e for e in db.scalars(select(Evidence).where(Evidence.entidade_tipo == "company",
                                                                    Evidence.entidade_id == c.id))}
        assert {"cnpj", "razao_social", "uf", "municipio", "porte", "cnae_principal", "situacao_cadastral"} <= set(ev)
        assert all(e.fonte == "cnpj_receita" and e.url and 0 < float(e.confianca) <= 1 for e in ev.values())
    assert float(db.scalar(select(Evidence.confianca).where(Evidence.campo == "email_cadastral"))) <= 0.3  # costuma ser do contador
    assert n(db, ComplianceRecord, ComplianceRecord.entidade_tipo == "company") == 2
    assert n(db, ComplianceRecord, ComplianceRecord.entidade_tipo == "contact") == 3


def test_filtro_por_cidade_usa_arquivo_de_municipios(db, world, tmp_path):
    write_set(tmp_path)
    cnpj_mod.import_cnpj(db, world.a.id, icp(db, world.a, municipios=["Campinas"]), tmp_path, cap=1000)
    db.commit()
    assert [c.nome_fantasia for c in db.scalars(select(Company))] == ["BETA GESTAO"]


def test_reimportar_nao_duplica(db, world, tmp_path):
    write_set(tmp_path)
    perfil = icp(db, world.a)
    cnpj_mod.import_cnpj(db, world.a.id, perfil, tmp_path, cap=1000)
    db.commit()
    antes = (n(db, Company), n(db, Contact), n(db, Evidence), n(db, ComplianceRecord), n(db, ContactRole))
    res = cnpj_mod.import_cnpj(db, world.a.id, perfil, tmp_path, cap=1000)
    db.commit()
    assert res["criadas"] == 0 and res["atualizadas"] == 0 and res["socios"] == 0
    assert (n(db, Company), n(db, Contact), n(db, Evidence), n(db, ComplianceRecord), n(db, ContactRole)) == antes


def test_limite_e_avisos(db, world, tmp_path):
    write_set(tmp_path)
    res = cnpj_mod.import_cnpj(db, world.a.id, icp(db, world.a, portes=["MEI"]), tmp_path, cap=1)
    assert res["encontrados"] == 1 and any("Limite" in a for a in res["avisos"]) and any("MEI" in a for a in res["avisos"])


def test_erros_de_entrada(db, world, tmp_path):
    with pytest.raises(cnpj_mod.CnpjError, match="não encontrados"):
        cnpj_mod.import_cnpj(db, world.a.id, icp(db, world.a), tmp_path / "vazio", cap=10)
    write_set(tmp_path)
    with pytest.raises(cnpj_mod.CnpjError, match="CNAE ou um estado"):
        cnpj_mod.import_cnpj(db, world.a.id, icp(db, world.a, cnaes=[], ufs=[]), tmp_path, cap=10)
    with pytest.raises(cnpj_mod.CnpjError, match="foi encontrada"):
        cnpj_mod.import_cnpj(db, world.a.id, icp(db, world.a, municipios=["Cidade Que Nao Existe"]), tmp_path, cap=10)


def test_nome_bonito():
    assert cnpj_mod.nice_name("MARIA DA SILVA DOS SANTOS") == "Maria da Silva dos Santos"
    assert cnpj_mod.fmt_cnae("7020400") == "7020-4/00"


# ---------- job (acompanhamento, idempotência, falha) ----------
def test_job_registra_progresso_e_e_idempotente(db, world, tmp_path):
    write_set(tmp_path)
    perfil = icp(db, world.a)
    r1 = run_cnpj(db, world.a.id, perfil, tmp_path)
    assert not r1["repetido"] and r1["criadas"] == 2
    job = db.get(JobRun, r1["job_id"])
    db.refresh(job)
    assert job.status == "concluido" and job.tipo == "coleta_cnpj" and job.contadores["criadas"] == 2
    r2 = run_cnpj(db, world.a.id, perfil, tmp_path)
    assert r2["repetido"] and r2["job_id"] == r1["job_id"]
    assert n(db, Company) == 2


def test_job_com_erro_esperado_fica_falho_e_pode_repetir(db, world, tmp_path):
    perfil = icp(db, world.a)
    r = run_cnpj(db, world.a.id, perfil, tmp_path / "nada")
    assert "não encontrados" in r["erro"]
    job = db.get(JobRun, r["job_id"])
    db.refresh(job)
    assert job.status == "falhou" and "não encontrados" in job.contadores["erro"]
    write_set(tmp_path / "agora")
    r2 = run_cnpj(db, world.a.id, perfil, tmp_path / "agora")  # novo job: o anterior falhou
    assert not r2["repetido"] and r2["criadas"] == 2


def test_isolamento_entre_workspaces(db, world, tmp_path):
    write_set(tmp_path)
    cnpj_mod.import_cnpj(db, world.a.id, icp(db, world.a), tmp_path, cap=1000)
    cnpj_mod.import_cnpj(db, world.b.id, icp(db, world.b), tmp_path, cap=1000)  # mesmo CNPJ em outro workspace: permitido
    db.commit()
    assert n(db, Company, Company.workspace_id == world.a.id) == n(db, Company, Company.workspace_id == world.b.id) == 2
