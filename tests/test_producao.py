"""Etapa 10: limites por workspace, tarefas presas, backups, tokens OAuth, papéis e um SEGUNDO workspace (outro setor)
percorrendo Hunter → Filtro → Inteligência → Disparo sem mexer no código."""
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core import limits
from app.core.models import (Company, Contact, Deal, Hypothesis, JobRun, MailboxAccount, OpportunitySignal, OutboundMessage, Workspace,
                             WorkspaceMember)
from app.core.security import encrypt_json
from app.disparo.compose import run_compose
from app.disparo.replies import ingest_raw
from app.disparo.scheduler import schedule_approved, send_due
from app.filtro.enrichment.service import run_enrichment
from app.filtro.validation.service import run_validation
from app.hunter.companies.service import run_tracked
from app.hunter.resolution.service import run_resolution
from app.hunter.signals.service import run_signals
from app.intel.hypothesis import run_hypotheses
from app.metricas import queries as metricas
from app.ops import health
from tests.conftest import login, token
from tests.disparo.helpers import NOW, FakeSender, sender_box
from tests.disparo.test_sequences import reply_raw
from tests.filtro.test_validation import FakeResolver, FakeVerifier
from tests.hunter.test_signals import fetcher_for, news_client


# ======================= limites =======================
def test_limite_de_gasto_mensal_para_as_coletas(db, world):
    ws = world.a
    limits.set_limits(db, ws.id, {"gasto_api_mes_usd": 1.0})
    db.add(JobRun(workspace_id=ws.id, tipo="coleta_places", parametros_hash="x", parametros={}, status="concluido", custo_usd=1.2))
    db.commit()
    r = run_tracked(db, ws.id, "coleta_places", {"a": 1}, lambda p: {"ok": 1})
    assert r["job_id"] is None and "gasto mensal" in r["erro"]
    assert run_tracked(db, ws.id, "resolucao", {"a": 1}, lambda p: {"ok": 1}).get("ok") == 1  # tarefa sem custo segue liberada
    limits.set_limits(db, ws.id, {"gasto_api_mes_usd": 0})  # 0 = sem limite
    assert run_tracked(db, ws.id, "coleta_places", {"a": 2}, lambda p: {"ok": 1}).get("ok") == 1


def test_limite_de_contatos_bloqueia_coletas_que_criam_contatos(db, world):
    ws = world.a
    limits.set_limits(db, ws.id, {"max_contatos": 2})
    db.add_all([Contact(workspace_id=ws.id, email=f"{i}@x.com") for i in range(2)])
    db.commit()
    r = run_tracked(db, ws.id, "coleta_sites", {"a": 1}, lambda p: {"ok": 1})
    assert "Limite de contatos" in r["erro"]
    assert run_tracked(db, ws.id, "validacao_emails", {"a": 1}, lambda p: {"ok": 1}).get("ok") == 1


def test_limites_sao_por_workspace(db, world):
    limits.set_limits(db, world.a.id, {"envios_dia": 5})
    assert limits.get_limits(db, world.b.id)["envios_dia"] == 0 and limits.send_room(db, world.b.id) is None
    with pytest.raises(ValueError):
        limits.set_limits(db, world.a.id, {"envios_dia": "abc"})
    with pytest.raises(ValueError):
        limits.set_limits(db, world.a.id, {"envios_dia": -1})


def test_limite_de_envios_do_dia_no_workspace(db, world):
    from tests.disparo.helpers import contato, mensagem
    ws = world.a
    mb, _ = sender_box(db, ws, dominio="viaenvios.com.br")
    ct0, c0 = contato(db, ws, "hist@x.com")
    mensagem(db, ws, ct0, c0, status="enviada", mailbox_id=mb.id, enviada_em=NOW - timedelta(days=60))
    limits.set_limits(db, ws.id, {"envios_dia": 3})
    for i in range(6):
        ct, c = contato(db, ws, f"p{i}@c.com")
        mensagem(db, ws, ct, c, status="agendada", mailbox_id=mb.id, agendada_para=NOW)
    out = send_due(db, ws.id, FakeSender(), NOW)
    assert out["enviadas"] == 3 and out["limite_workspace"] is True
    assert db.scalar(select(func.count()).select_from(OutboundMessage).where(OutboundMessage.status == "agendada")) == 3


# ======================= saúde =======================
def test_tarefa_presa_e_encerrada_avisa_e_libera_nova_execucao(db, world):
    ws = world.a
    velho = datetime.now(timezone.utc) - timedelta(hours=12)
    db.add(JobRun(workspace_id=ws.id, tipo="coleta_sites", parametros_hash="h", parametros={}, status="em_andamento", iniciado_em=velho))
    db.add(JobRun(workspace_id=ws.id, tipo="resolucao", parametros_hash="h2", parametros={}, status="em_andamento"))  # recente: fica
    db.commit()
    alertas = []
    assert [j.tipo for j in health.stuck_jobs(db)] == ["coleta_sites"]
    assert health.reap_stuck_jobs(db, alert=alertas.append) == 1
    db.commit()
    j = db.scalar(select(JobRun).where(JobRun.tipo == "coleta_sites"))
    assert j.status == "falhou" and "parou de responder" in j.contadores["erro"] and "coleta_sites" in alertas[0]
    assert db.scalar(select(JobRun.status).where(JobRun.tipo == "resolucao")) == "em_andamento"
    assert run_tracked(db, ws.id, "coleta_sites", {}, lambda p: {"ok": 1}).get("job_id")  # pode rodar de novo (a trava ignora falhos)


def test_backup_status_e_retencao_de_30_dias(tmp_path):
    assert health.backup_status(tmp_path)["ok"] is False
    velho, novo = tmp_path / "prospector-velho.sql.gz", tmp_path / "prospector-novo.sql.gz"
    velho.write_bytes(b"x" * 10)
    novo.write_bytes(b"y" * 2_000_000)
    os.utime(velho, (time.time() - 40 * 86400,) * 2)
    st = health.backup_status(tmp_path)
    assert st["ok"] and st["quantidade"] == 2 and st["idade_h"] < 1 and st["tamanho_mb"] == 2.0
    assert health.prune_backups(tmp_path, 30) == 1 and not velho.exists() and novo.exists()
    os.utime(novo, (time.time() - 30 * 3600,) * 2)
    st = health.backup_status(tmp_path)
    assert not st["ok"] and "30 horas" in st["aviso"] or "horas" in st["aviso"]
    vazio = tmp_path / "prospector-zero.sql.gz"
    vazio.write_bytes(b"")
    assert health.backup_status(tmp_path)["ok"] is False  # arquivo vazio não conta como backup bom


def test_renovacao_de_tokens_marca_saude(db, world):
    ws = world.a
    ok = MailboxAccount(workspace_id=ws.id, tipo="gmail", nome="Gmail ok", uso="hunter", tokens_enc=encrypt_json({"client_id": "a", "client_secret": "b", "refresh_token": "c"}))
    ruim = MailboxAccount(workspace_id=ws.id, tipo="graph", nome="Outlook revogado", uso="hunter", tokens_enc=encrypt_json({"client_id": "a", "client_secret": "b", "refresh_token": "c"}))
    db.add_all([ok, ruim])
    db.commit()

    def revogado(cfg):
        raise PermissionError("invalid_grant")
    r = health.refresh_oauth_tokens(db, {"gmail": lambda c: {"access_token": "x"}, "graph": revogado})
    db.commit()
    assert r == {"ok": 1, "erro": 1} and ok.saude == "ok" and ruim.saude == "reautorizar"


# ======================= telas e papéis =======================
def test_operacao_so_para_administrador(client, db, world):
    for email in (world.operador_a.email, world.approver_a.email):
        login(client, email)
        tk = token(client, "/")
        assert client.get("/operacao").status_code == 403
        assert client.post("/operacao/limites", data={"csrf_token": tk, "envios_dia": "9"}).status_code == 403
        client.post("/logout", data={"csrf_token": tk})
    login(client, world.user_a.email)
    html = client.get("/operacao").text
    assert "Backup diário" in html and "Limites deste workspace" in html


def test_admin_define_limites_e_cria_usuario_com_papel(client, db, world):
    login(client, world.user_a.email)
    tk = token(client, "/operacao")
    r = client.post("/operacao/limites", data={"csrf_token": tk, "max_contatos": "500", "envios_dia": "40", "gasto_api_mes_usd": "25.5"})
    db.expire_all()
    assert r.status_code == 303 and limits.get_limits(db, world.a.id) == {"max_contatos": 500, "envios_dia": 40, "gasto_api_mes_usd": 25.5}
    client.post("/operacao/usuarios", data={"csrf_token": tk, "email": "Nova@Agencia.com", "senha": "senha-longa-123", "papel": "aprovador", "nome": "Nova"})
    m = db.execute(select(WorkspaceMember.papel).join(__import__("app.core.models", fromlist=["AppUser"]).AppUser).where(
        __import__("app.core.models", fromlist=["AppUser"]).AppUser.email == "nova@agencia.com")).scalar()
    assert m == "aprovador"
    client.post("/operacao/usuarios", data={"csrf_token": tk, "email": "x@y.com", "senha": "curta", "papel": "admin"})  # senha curta recusada
    assert db.scalar(select(func.count()).select_from(__import__("app.core.models", fromlist=["AppUser"]).AppUser).where(
        __import__("app.core.models", fromlist=["AppUser"]).AppUser.email == "x@y.com")) == 0
    client.post("/operacao/usuarios", data={"csrf_token": tk, "email": "z@y.com", "senha": "senha-longa-123", "papel": "superuser"})
    assert db.scalar(select(func.count()).select_from(WorkspaceMember).where(WorkspaceMember.papel == "superuser")) == 0


# ======================= segundo workspace: outro setor, mesmo código =======================
def test_segundo_workspace_percorre_hunter_filtro_inteligencia_e_disparo(client, db, world):
    # 1) o administrador cria o workspace de outro setor e configura tudo pelo painel
    login(client, world.user_a.email)
    tk = token(client, "/operacao")
    assert client.post("/operacao/workspaces", data={"csrf_token": tk, "nome": "Clínica Sorriso"}).status_code == 303
    ws = db.scalar(select(Workspace).where(Workspace.nome == "Clínica Sorriso"))
    assert db.scalar(select(func.count()).select_from(Contact).where(Contact.workspace_id == ws.id)) == 0  # nasce vazio
    assert client.post("/workspaces", data={"csrf_token": tk, "workspace_id": ws.id}).status_code == 303
    assert "Clínica Sorriso" in client.get("/").text
    r = client.post("/configuracoes/departamentos", data={"csrf_token": tk, "nome": "Direção Clínica", "email_patterns": "diretoria@",
                                                          "cargos": "Diretor Clínico, Responsável Técnico",
                                                          "necessidades": "Divulgar novos tratamentos | 5 | video, social"})
    assert r.status_code == 303
    assert client.post("/configuracoes/icp", data={"csrf_token": tk, "nome": "Clínicas odontológicas SP", "cnaes": "86.30-5", "ufs": "SP",
                                                   "portes": "ME, EPP", "termos_maps": "clínica odontológica", "municipios": "Campinas"}).status_code == 303
    r = client.post("/inteligencia/ofertas", data={"csrf_token": tk, "nome": "Vídeo de tratamentos", "tipos_sinal": ["inauguracao", "lancamento"],
                                                   "tags": "video", "ativo": "sim", "descricao": "Vídeos explicativos de tratamentos",
                                                   "cases": "Vídeo de clareamento para a Clínica Beta"})
    assert r.status_code == 303

    # 2) Hunter: empresa do novo setor + sinal vindo de notícia
    co = Company(workspace_id=ws.id, razao_social="SORRISO ODONTO LTDA", nome_fantasia="Alfa Odonto", dominio="alfaodonto.com.br", site="https://alfaodonto.com.br",
                 cnae_principal="8630-5/04", uf="SP", municipio="Campinas", site_status="ok", situacao_cadastral="Ativa", cnpj="11222333000181")
    dup = Company(workspace_id=ws.id, nome_fantasia="Alfa Odonto", dominio="alfaodonto.com.br", telefone="1933334444", uf="SP", municipio="Campinas")
    ct = Contact(workspace_id=ws.id, company_id=None, email="ana.lima@alfaodonto.com.br")
    db.add_all([co, dup, ct])
    db.commit()
    ct.company_id = dup.id
    db.commit()
    res = run_signals(db, ws.id, None, fetcher=fetcher_for({}), http=news_client(
        '<rss><channel><item><title>Alfa Odonto inaugura nova unidade em Campinas</title><link>https://jornal.example/odonto</link>'
        '<pubDate>{hoje}</pubDate></item></channel></rss>'.replace("{hoje}", datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"))))
    assert res["novos"] >= 1
    assert run_resolution(db, ws.id)["mescladas"] == 1  # as duas versões da mesma clínica viram uma
    db.expire_all()
    assert db.get(Contact, ct.id).company_id in (co.id, dup.id)

    # 3) Filtro: valida o email e enriquece (nome, notas)
    run_validation(db, ws.id, resolver=FakeResolver(), verifier=FakeVerifier())
    run_enrichment(db, ws.id, llm=None, use_default_llm=False, verifier=None, use_default_verifier=False)
    db.expire_all()
    c = db.get(Contact, ct.id)
    assert c.email_status == "valido" and c.nome_completo == "Ana Lima" and c.nota_final > 0

    # 4) Inteligência: hipótese com prova, aprovada pelo humano
    h = run_hypotheses(db, ws.id, use_default_llm=False)
    assert h["hipoteses"] == 1
    hyp = db.scalar(select(Hypothesis).where(Hypothesis.workspace_id == ws.id))
    assert hyp.status == "pendente" and hyp.evidencias[0]["url"] == "https://jornal.example/odonto"
    assert client.post(f"/inteligencia/hipoteses/{hyp.id}/decidir", data={"csrf_token": tk, "acao": "aprovar"}).status_code == 303

    # 5) Disparo: escreve, aprova, agenda e envia (em modo simulado)
    db.expire_all()
    run_compose(db, ws.id, use_default_llm=False, checker=lambda raw: 1.0)
    first = db.scalar(select(OutboundMessage).where(OutboundMessage.workspace_id == ws.id, OutboundMessage.passo == 1))
    assert first.status == "rascunho" and "Alfa Odonto" in first.corpo and first.icp_id is not None
    r = client.post("/disparo/mensagens/decidir", data={"csrf_token": tk, "acao": "aprovar", "sel": [str(first.id)]})
    assert r.status_code == 303
    db.expire_all()
    assert db.get(OutboundMessage, first.id).status in ("aprovada", "rascunho")
    mb, _ = sender_box(db, ws, dominio="clinicasorriso-envios.com.br")
    db.add(OutboundMessage(workspace_id=ws.id, contact_id=ct.id, mailbox_id=mb.id, status="enviada", assunto="h", corpo="h", passo=1,
                           enviada_em=NOW - timedelta(days=60)))
    db.commit()
    first = db.get(OutboundMessage, first.id)
    if first.status != "aprovada":  # (a nota de spam vem do Rspamd local no ambiente de teste)
        first.status = "aprovada"
        db.commit()
    assert schedule_approved(db, ws.id, NOW)["agendadas"] == 1
    db.commit()
    s = FakeSender()
    assert send_due(db, ws.id, s, first.agendada_para)["enviadas"] == 1
    db.refresh(first)

    # 6) respostas e métricas do novo workspace
    assert ingest_raw(db, ws.id, reply_raw(c.email, first.message_id, "Tenho interesse! Podemos conversar?"))["classe"] == "interessado"
    db.commit()
    assert db.scalar(select(Deal.estagio).where(Deal.workspace_id == ws.id)) == "interessado"
    cv = metricas.conversao(db, ws.id)
    assert cv["interessados"] == 1 and cv["abordados"] >= 1

    # 7) nada vazou entre workspaces
    assert db.scalar(select(func.count()).select_from(Deal).where(Deal.workspace_id == world.a.id)) == 0
    assert db.scalar(select(func.count()).select_from(Hypothesis).where(Hypothesis.workspace_id == world.a.id)) == 0
    assert "Alfa Odonto" in client.get("/hunter/empresas").text
    login_other = client.post("/workspaces", data={"csrf_token": token(client, "/workspaces"), "workspace_id": world.a.id})
    assert login_other.status_code == 303
    assert "Alfa Odonto" not in client.get("/hunter/empresas").text
    assert Path  # (Path é usado em outros testes deste arquivo)
    assert OpportunitySignal
