"""Critérios de aceite da Etapa 1: contatos sem duplicados, idempotência, conformidade, só metadados."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text

from app.core.models import (Company, ComplianceRecord, Contact, Evidence, JobRun, MailInteraction)
from app.hunter.mailbox.mbox import MboxAdapter
from app.hunter.mailbox.service import file_sha, scan_mailbox
from app.hunter.mailbox.signature import SignatureInfo
from tests.hunter.helpers import (OWN, SEGREDO_ASSUNTO, SEGREDO_CORPO, build_1000, make_msg, write_mbox)


def run(db, mb, path, noise, llm=None, live=False, hint=None):
    sha = None if live else file_sha(path)
    return scan_mailbox(db, mb, MboxAdapter(path, hint), own_addresses={OWN}, noise=noise, llm=llm,
                        content_sha=sha)


def count(db, model, *where):
    return db.scalar(select(func.count()).select_from(model).where(*where))


def test_1000_mensagens_geram_contatos_sem_duplicatas(db, world, mailbox, noise, tmp_path):
    gab = build_1000(tmp_path / "caixa.mbox")
    mb = mailbox(tmp_path / "caixa.mbox")
    res = run(db, mb, tmp_path / "caixa.mbox", noise)
    db.commit()
    assert res.lidas == 1000 and res.ruido == gab["ruido"]
    emails = list(db.scalars(select(Contact.email).where(Contact.workspace_id == world.a.id)))
    assert len(emails) == len(set(emails)) == len(gab["externos"]) > 250
    assert set(emails) == gab["externos"] and OWN not in emails
    # remetentes de robô/lista/banco nunca viram contato
    assert not any("noreply" in e or "itau" in e or "novidades@" in e for e in emails)
    assert res.interacoes_novas == count(db, MailInteraction) == 900  # 800 recebidas + 100 enviadas


def test_reprocessar_nao_cria_nada_novo(db, world, mailbox, noise, tmp_path):
    build_1000(tmp_path / "caixa.mbox")
    mb = mailbox(tmp_path / "caixa.mbox")
    run(db, mb, tmp_path / "caixa.mbox", noise)
    db.commit()
    antes = (count(db, Contact), count(db, MailInteraction), count(db, Evidence), count(db, ComplianceRecord))
    again = run(db, mb, tmp_path / "caixa.mbox", noise)  # mesmo arquivo: job repetido
    assert again.repetido and again.lidas == 0
    # varredura "ao vivo" (job novo, relê tudo): a trava por mensagem/contato impede duplicar
    full = run(db, mb, tmp_path / "caixa.mbox", noise, live=True)
    db.commit()
    assert not full.repetido and full.lidas == 1000
    assert full.interacoes_novas == 0 and full.contatos_novos == 0
    assert (count(db, Contact), count(db, MailInteraction), count(db, Evidence), count(db, ComplianceRecord)) == antes


def test_todo_contato_tem_conformidade_e_evidencia(db, world, mailbox, noise, tmp_path):
    build_1000(tmp_path / "caixa.mbox")
    run(db, mailbox(tmp_path / "caixa.mbox"), tmp_path / "caixa.mbox", noise)
    db.commit()
    ids = set(db.scalars(select(Contact.id)))
    comp = db.scalars(select(ComplianceRecord).where(ComplianceRecord.entidade_tipo == "contact")).all()
    assert {c.entidade_id for c in comp} == ids and len(comp) == len(ids) > 250
    assert all(c.origem_dado == "caixa_email" and c.finalidade and c.base_legal and c.retencao_ate for c in comp)
    ev = db.scalars(select(Evidence).where(Evidence.campo == "email")).all()
    assert {e.entidade_id for e in ev} == ids
    assert all(e.fonte == "caixa_email:mbox" and e.url.startswith("mailbox://") and 0 < float(e.confianca) <= 1
               for e in ev)


def test_corpo_e_assunto_nunca_sao_gravados(db, world, mailbox, noise, tmp_path):
    build_1000(tmp_path / "caixa.mbox")
    run(db, mailbox(tmp_path / "caixa.mbox"), tmp_path / "caixa.mbox", noise)
    db.commit()
    for tabela in ("contact", "mail_interaction", "evidence", "compliance_record", "job_run", "mailbox_account"):
        dump = db.scalar(text(f"SELECT coalesce(string_agg(row_to_json(t)::text, ' '), '') FROM {tabela} t"))
        assert SEGREDO_CORPO not in dump and SEGREDO_ASSUNTO not in dump, tabela
    # a assinatura (trecho curto) é permitida; o texto antes dela, não
    c = db.scalar(select(Contact).where(Contact.email == "pessoa5@empresa5.com.br"))
    assert c.assinatura_trecho and len(c.assinatura_trecho) <= 400 and "tudo bem" not in c.assinatura_trecho


def test_assinatura_preenche_nome_cargo_telefone_site(db, world, mailbox, noise, tmp_path):
    build_1000(tmp_path / "caixa.mbox")
    run(db, mailbox(tmp_path / "caixa.mbox"), tmp_path / "caixa.mbox", noise)
    db.commit()
    c = db.scalar(select(Contact).where(Contact.email == "pessoa7@empresa7.com.br"))
    assert (c.nome_completo, c.cargo) == ("Pessoa Numero7", "Gerente de Marketing")
    assert c.telefone == "34999990007" and c.site == "www.empresa7.com.br" and c.primeiro_nome == "Pessoa"
    assert c.dominio_tipo == "corporativo" and c.origem == "caixa_email"
    campos = set(db.scalars(select(Evidence.campo).where(Evidence.entidade_id == c.id)))
    assert {"email", "cargo", "telefone", "site"} <= campos


def test_direcao_e_nota_de_relacionamento(db, world, mailbox, noise, tmp_path):
    agora = datetime.now(timezone.utc)
    msgs = []
    # A: conversa de mão dupla recente, com assuntos comerciais
    for i in range(6):
        msgs.append(make_msg(mid=f"a{i}", de="ana@clientea.com.br", para=OWN, date=agora - timedelta(days=i),
                             assunto="Proposta e contrato", corpo="Segue orçamento e nota fiscal na reunião"))
        msgs.append(make_msg(mid=f"a-e{i}", de=OWN, para="ana@clientea.com.br", date=agora - timedelta(days=i),
                             assunto="Re: Proposta", corpo="ok"))
    # B: um único email recebido, antigo
    msgs.append(make_msg(mid="b0", de="bia@clienteb.com.br", para=OWN, date=agora - timedelta(days=500),
                         assunto="Oi", corpo="oi"))
    write_mbox(tmp_path / "c.mbox", msgs)
    run(db, mailbox(tmp_path / "c.mbox"), tmp_path / "c.mbox", noise)
    db.commit()
    a = db.scalar(select(Contact).where(Contact.email == "ana@clientea.com.br"))
    b = db.scalar(select(Contact).where(Contact.email == "bia@clienteb.com.br"))
    assert (a.qtd_enviados, a.qtd_recebidos) == (6, 6) and (b.qtd_enviados, b.qtd_recebidos) == (0, 1)
    assert a.relationship_score > 70 > 20 > b.relationship_score >= 0
    assert a.ultima_interacao is not None and a.ultima_interacao > b.ultima_interacao


def test_pasta_so_enviados_e_vinculo_com_empresa(db, world, mailbox, noise, tmp_path):
    db.add(Company(workspace_id=world.a.id, razao_social="Cliente A", dominio="clientea.com.br"))
    db.commit()
    write_mbox(tmp_path / "e.mbox", [make_msg(mid="e1", de=OWN, para="Ana Clara <ana@clientea.com.br>, bot@itau.com.br",
                                              date=datetime.now(timezone.utc), assunto="x", corpo="y")])
    mb = mailbox(tmp_path / "e.mbox", hint="enviado")
    run(db, mb, tmp_path / "e.mbox", noise, hint="enviado")
    db.commit()
    c = db.scalars(select(Contact)).all()
    assert [x.email for x in c] == ["ana@clientea.com.br"]  # banco ignorado
    assert c[0].qtd_enviados == 1 and c[0].company_id == db.scalar(select(Company.id)) and c[0].nome_completo == "Ana Clara"


def test_job_registra_contadores_e_custo_da_ia(db, world, mailbox, noise, tmp_path):
    class FakeLLM:
        calls = 0

        def extract(self, trecho):
            FakeLLM.calls += 1
            return SignatureInfo(nome="Maria Dos Santos", cargo="Diretora", trecho=trecho), 0.002

    agora = datetime.now(timezone.utc)
    sem_regra = "Valeu!\n\nmaria@x.com.br | cel 34 98888 7777\nsetor de compras"  # as regras não acham nome/cargo
    write_mbox(tmp_path / "i.mbox", [make_msg(mid="i1", de="maria@x.com.br", para=OWN, date=agora, assunto="s",
                                              corpo=sem_regra)])
    mb = mailbox(tmp_path / "i.mbox")
    res = run(db, mb, tmp_path / "i.mbox", noise, llm=FakeLLM())
    db.commit()
    job = db.get(JobRun, res.job_id)
    assert FakeLLM.calls == 1 and res.assinaturas_ia == 1 and abs(res.custo_usd - 0.002) < 1e-9
    assert job.status == "concluido" and float(job.custo_usd) == 0.002
    assert job.contadores["contatos_novos"] == 1 and job.contadores["assinaturas_ia"] == 1
    c = db.scalar(select(Contact))
    assert c.nome_completo == "Maria Dos Santos" and c.cargo == "Diretora" and c.telefone == "34988887777"


def test_ia_com_erro_nao_derruba_a_varredura(db, world, mailbox, noise, tmp_path):
    class Quebrada:
        def extract(self, trecho):
            raise RuntimeError("API fora do ar")

    write_mbox(tmp_path / "q.mbox", [make_msg(mid="q1", de="zé@x.com.br".replace("é", "e"), para=OWN,
                                              date=datetime.now(timezone.utc), assunto="s",
                                              corpo="oi\n\nfulano\nsem nada util")])
    res = run(db, mailbox(tmp_path / "q.mbox"), tmp_path / "q.mbox", noise, llm=Quebrada())
    assert res.contatos_novos == 1 and res.custo_usd == 0
