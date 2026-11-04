import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.compliance import create_compliance_record
from app.core.jobs import finish_job, start_job
from app.core.models import ComplianceRecord, Department, Evidence, JobRun, Workspace
from app.core.provenance import record_evidence
from app.core.seed import WORKSPACE_VIA, seed_workspace_via


# ---------- proveniência ----------
def test_evidence_registra_fonte_e_confianca(db, world):
    ev = record_evidence(db, workspace_id=world.a.id, entity_type="contact", entity_id=1, field="email",
                         value="x@y.com.br", source="importacao_manual", url="csv://a.csv", confidence=0.7)
    assert ev.fonte == "importacao_manual" and float(ev.confianca) == 0.7 and ev.coletado_em is not None


def test_evidence_repetida_nao_duplica(db, world):
    kw = dict(workspace_id=world.a.id, entity_type="contact", entity_id=1, field="cargo", value="CEO", source="s")
    a, b = record_evidence(db, **kw), record_evidence(db, **kw)
    assert a.id == b.id
    assert db.scalar(select(func.count()).select_from(Evidence)) == 1


@pytest.mark.parametrize("conf", [-0.1, 1.5])
def test_evidence_confianca_fora_da_faixa(db, world, conf):
    with pytest.raises(ValueError):
        record_evidence(db, workspace_id=world.a.id, entity_type="c", entity_id=1, field="f", value="v",
                        source="s", confidence=conf)


def test_evidence_confianca_barrada_no_banco(db, world):
    db.add(Evidence(workspace_id=world.a.id, entidade_tipo="c", entidade_id=1, campo="f", valor="v",
                    fonte="s", confianca=2))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# ---------- jobs ----------
def test_job_mesmos_parametros_nao_duplica(db, world):
    id1, novo1 = start_job(db, "coleta", {"uf": "MG", "n": 1}, world.a.id)
    id2, novo2 = start_job(db, "coleta", {"n": 1, "uf": "MG"}, world.a.id)  # ordem das chaves não importa
    assert id1 == id2 and novo1 and not novo2


def test_job_parametros_diferentes_ou_workspace_diferente_criam_novo(db, world):
    id1, _ = start_job(db, "coleta", {"uf": "MG"}, world.a.id)
    id2, n2 = start_job(db, "coleta", {"uf": "SP"}, world.a.id)
    id3, n3 = start_job(db, "coleta", {"uf": "MG"}, world.b.id)
    assert len({id1, id2, id3}) == 3 and n2 and n3


def test_job_global_sem_workspace_tambem_e_idempotente(db, world):
    id1, _ = start_job(db, "limpeza", {"x": 1})
    id2, novo = start_job(db, "limpeza", {"x": 1})
    assert id1 == id2 and not novo


def test_job_finalizado_guarda_custo_e_contadores(db, world):
    jid, _ = start_job(db, "coleta", {"a": 1}, world.a.id)
    job = finish_job(db, jid, {"empresas": 10}, custo_usd=0.0123)
    assert job.status == "concluido" and float(job.custo_usd) == 0.0123 and job.finalizado_em is not None
    assert job.contadores == {"empresas": 10}


def test_job_que_falhou_pode_rodar_de_novo(db, world):
    id1, _ = start_job(db, "coleta", {"a": 1}, world.a.id)
    finish_job(db, id1, {}, status="falhou")
    id2, novo = start_job(db, "coleta", {"a": 1}, world.a.id)
    assert novo and id2 != id1


def test_banco_barra_job_duplicado(db, world):
    db.add_all([JobRun(workspace_id=world.a.id, tipo="t", parametros_hash="h"),
                JobRun(workspace_id=world.a.id, tipo="t", parametros_hash="h")])
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# ---------- conformidade ----------
def test_compliance_guarda_origem_finalidade_base_e_retencao(db, world):
    rec = create_compliance_record(db, workspace_id=world.a.id, entity_type="contact", entity_id=7,
                                   origem="importacao_manual")
    assert rec.origem_dado == "importacao_manual" and rec.finalidade == "prospeccao_b2b"
    assert rec.base_legal and rec.retencao_ate > rec.coletado_em


def test_compliance_idempotente(db, world):
    kw = dict(workspace_id=world.a.id, entity_type="contact", entity_id=7, origem="x")
    assert create_compliance_record(db, **kw).id == create_compliance_record(db, **kw).id
    assert db.scalar(select(func.count()).select_from(ComplianceRecord)) == 1


# ---------- semente ----------
def test_semente_cria_via_e_10_departamentos_com_necessidades(db):
    ws = seed_workspace_via(db)
    deps = db.scalars(select(Department).where(Department.workspace_id == ws.id)).all()
    assert ws.nome == WORKSPACE_VIA and len(deps) == 10
    nomes = {d.nome for d in deps}
    assert {"Marketing e Comunicação", "RH e Gente", "SST e Segurança do Trabalho", "Comercial e Vendas",
            "Eventos", "Operações e Qualidade", "Diretoria", "Compras e Financeiro", "TI", "Jurídico"} == nomes
    assert all(d.necessidades_previstas and d.cargos and d.email_patterns for d in deps)
    n = deps[0].necessidades_previstas[0]
    assert {"necessidade", "relevancia", "ofertas_relacionadas"} <= set(n)


def test_semente_idempotente(db):
    seed_workspace_via(db)
    db.commit()
    seed_workspace_via(db)
    db.commit()
    assert db.scalar(select(func.count()).select_from(Workspace)) == 1
    assert db.scalar(select(func.count()).select_from(Department)) == 10


# ---------- fila ----------
def test_worker_ping():
    from app.workers.worker import WorkerSettings, ping

    assert ping in WorkerSettings.functions
    assert asyncio.run(ping({})) == "pong"
