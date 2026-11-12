"""Etapa 8: sequência dia 0/4/10 na mesma conversa, leitura e classificação de respostas, ações por resposta."""
import random
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest
from sqlalchemy import func, select

from app.core.models import Contact, CrmEvent, Deal, InboundReply, OutboundMessage, Suppression, Task
from app.disparo.replies import classify_rules, ingest_raw, parse_reply, parse_return_date, strip_quoted
from app.disparo.scheduler import schedule_approved, send_due
from tests.disparo.helpers import NOW, FakeSender, contato, mensagem, sender_box
from tests.disparo.test_senders import DSN_HARD


def sequencia(db, ws, email="ana@cliente.com.br", uf="SP", aprovada=True):
    """Cria contato + 3 passos aprovados (como depois da aprovação em lote) e uma caixa pronta e aquecida."""
    mb = db.scalar(select(__import__("app.core.models", fromlist=["MailboxAccount"]).MailboxAccount).where(
        __import__("app.core.models", fromlist=["MailboxAccount"]).MailboxAccount.workspace_id == ws.id))
    if mb is None:
        mb, _ = sender_box(db, ws, dominio="viaenvios.com.br")
        ct0, c0 = contato(db, ws, "historico@x.com")
        mensagem(db, ws, ct0, c0, status="enviada", mailbox_id=mb.id, enviada_em=NOW - timedelta(days=60))
    ct, c = contato(db, ws, email, uf=uf)
    st = "aprovada" if aprovada else "rascunho"
    m1 = mensagem(db, ws, ct, c, status=st, passo=1, objetivo="sonda", assunto="Uma ideia")
    m1.thread_raiz_id = m1.id
    m2 = mensagem(db, ws, ct, c, status=st, passo=2, objetivo="seguimento", assunto="Re: Uma ideia", thread_raiz_id=m1.id)
    m3 = mensagem(db, ws, ct, c, status=st, passo=3, objetivo="ultimo_toque", assunto="Re: Uma ideia", thread_raiz_id=m1.id)
    db.commit()
    return ct, c, mb, (m1, m2, m3)


def reply_raw(from_addr, in_reply_to, body, subject="Re: Uma ideia", auto=False, references=None, mid="<r1@cliente.com.br>"):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = from_addr, "contato@viaenvios.com.br", subject, mid
    m["Date"] = "Mon, 09 Nov 2026 15:00:00 +0000"
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = references or in_reply_to
    if auto:
        m["Auto-Submitted"] = "auto-replied"
    m.set_content(body)
    return m.as_bytes()


def enviar_primeiro(db, ws, m1, sender, when=NOW):
    schedule_approved(db, ws.id, when, random.Random(1))
    db.commit()
    assert send_due(db, ws.id, sender, m1.agendada_para)["enviadas"] == 1
    db.refresh(m1)
    return m1


# ======================= sequência =======================
def test_sequencia_dia_0_4_10_na_mesma_conversa(db, world):
    ws = world.a
    ct, c, mb, (m1, m2, m3) = sequencia(db, ws)
    s = FakeSender()
    schedule_approved(db, ws.id, NOW, random.Random(1))
    db.commit()
    assert (m1.status, m2.status, m3.status) == ("agendada", "aprovada", "aprovada")  # seguimentos esperam o 1º ser enviado
    send_due(db, ws.id, s, m1.agendada_para)
    db.refresh(m1)
    assert m1.status == "enviada"
    schedule_approved(db, ws.id, m1.enviada_em + timedelta(days=1), random.Random(2))
    db.commit()
    assert m2.status == "agendada" and m2.agendada_para >= m1.enviada_em + timedelta(days=4) and m3.status == "aprovada"
    assert m2.mailbox_id == m1.mailbox_id  # mesma caixa, mesma conversa
    send_due(db, ws.id, s, m2.agendada_para)
    schedule_approved(db, ws.id, m2.enviada_em + timedelta(days=1), random.Random(3))
    db.commit()
    assert m3.status == "agendada" and m3.agendada_para >= m1.enviada_em + timedelta(days=10)
    send_due(db, ws.id, s, m3.agendada_para)
    assert [x.status for x in (m1, m2, m3)] == ["enviada"] * 3
    mids = [msg["Message-ID"] for _, msg in s.enviadas]
    assert s.enviadas[1][1]["In-Reply-To"] == mids[0] and s.enviadas[2][1]["In-Reply-To"] == mids[0]
    assert mids[0] in s.enviadas[2][1]["References"]


def test_seguimento_nao_sai_sem_o_anterior_enviado(db, world):
    ws = world.a
    ct, c, mb, (m1, m2, m3) = sequencia(db, ws)
    m1.status = "cancelada"
    db.commit()
    assert schedule_approved(db, ws.id, NOW + timedelta(days=30), random.Random(1))["agendadas"] == 0
    assert m2.status == "aprovada"


# ======================= classificação =======================
@pytest.mark.parametrize("texto,classe", [
    ("Olá! Tenho interesse, podemos conversar amanhã?", "interessado"),
    ("Agora não, volte a falar comigo no próximo semestre.", "nao_agora"),
    ("Não sou a pessoa certa. Fale com maria@cliente.com.br", "pessoa_errada"),
    ("Por favor remova meu email da lista, não quero receber.", "descadastro"),
    ("Já temos fornecedor e o orçamento está fechado.", "objecao"),
    ("Obrigado, recebido.", "outro"),
])
def test_classificacao_por_regras(texto, classe):
    assert classify_rules(texto)[0] == classe


def test_ausencia_automatica_e_data_de_retorno():
    assert classify_rules("Estarei fora do escritório até 20/11/2026.", "Resposta automática", True)[0] == "ausente"
    assert parse_return_date("volto em 20/11/2026", NOW).date().isoformat() == "2026-11-20"
    assert parse_return_date("sem data", NOW) is None
    assert strip_quoted("Sim, tenho interesse.\n\nEm seg, 9 nov, Fulano escreveu:\n> texto antigo") == "Sim, tenho interesse."


def test_ia_nao_pode_derrubar_descadastro(db, world):
    from tests.filtro.test_enrichment import FakeLLM
    from app.disparo.replies import classify
    p = parse_reply(reply_raw("a@x.com", "<a>", "Por favor, remova meu email."))
    llm = FakeLLM({"remova": '{"classe": "interessado"}'})
    assert classify(db, world.a.id, p, llm)[0] == "descadastro"
    p2 = parse_reply(reply_raw("a@x.com", "<a>", "Hmm, talvez no futuro, me explica melhor?"))
    llm2 = FakeLLM({"talvez": '{"classe": "interessado"}'})
    classe, _, custo = classify(db, world.a.id, p2, llm2)
    assert classe == "interessado" and custo > 0


# ======================= ações =======================
def _enviada(db, ws):
    ct, c, mb, (m1, m2, m3) = sequencia(db, ws)
    enviar_primeiro(db, ws, m1, FakeSender())
    return ct, m1, m2, m3


def test_interessado_cria_negocio_tarefa_e_cancela_seguimentos(db, world, monkeypatch):
    ws = world.a
    ct, m1, m2, m3 = _enviada(db, ws)
    r = ingest_raw(db, ws.id, reply_raw(ct.email, m1.message_id, "Tenho interesse! Podemos conversar?"))
    db.commit()
    assert r["tipo"] == "resposta" and r["classe"] == "interessado"
    d = db.scalar(select(Deal))
    assert d.estagio == "interessado" and d.contact_id == ct.id
    t = db.scalar(select(Task))
    assert "24h" in t.descricao and t.vence_em > datetime.now(timezone.utc) + timedelta(hours=23)
    assert (m2.status, m3.status) == ("cancelada", "cancelada")
    assert db.scalar(select(CrmEvent.status)) == "sem_destino"  # CRM ainda não configurado: registrado para reenviar depois
    # a mesma resposta não é processada duas vezes
    assert ingest_raw(db, ws.id, reply_raw(ct.email, m1.message_id, "Tenho interesse! Podemos conversar?"))["tipo"] == "ignorado"
    assert db.scalar(select(func.count()).select_from(Deal)) == 1 and db.scalar(select(func.count()).select_from(Task)) == 1


def test_nao_agora_cancela_e_agenda_90_dias(db, world):
    ws = world.a
    ct, m1, m2, m3 = _enviada(db, ws)
    ingest_raw(db, ws.id, reply_raw(ct.email, m1.message_id, "Agora não, volte a falar mais para frente."))
    db.commit()
    assert db.scalar(select(Deal.estagio)) == "nutricao" and m2.status == "cancelada"
    t = db.scalar(select(Task))
    assert timedelta(days=89) < t.vence_em - datetime.now(timezone.utc) < timedelta(days=91)


def test_pessoa_errada_cria_novo_contato_na_mesma_empresa(db, world):
    ws = world.a
    ct, m1, m2, m3 = _enviada(db, ws)
    ingest_raw(db, ws.id, reply_raw(ct.email, m1.message_id, "Não sou a pessoa certa. Fale com maria.souza@cliente.com.br por favor."))
    db.commit()
    novo = db.scalar(select(Contact).where(Contact.email == "maria.souza@cliente.com.br"))
    assert novo is not None and novo.company_id == ct.company_id and novo.origem == "indicacao_resposta"
    assert m2.status == "cancelada" and db.scalar(select(func.count()).select_from(Deal)) == 0


def test_ausente_pausa_ate_a_data_de_retorno(db, world):
    ws = world.a
    ct, m1, m2, m3 = _enviada(db, ws)
    volta = datetime(2026, 12, 1, tzinfo=timezone.utc)
    ingest_raw(db, ws.id, reply_raw(ct.email, m1.message_id, "Estou de férias, volto em 01/12/2026.", "Resposta automática: férias", auto=True))
    db.commit()
    assert m2.status == "aprovada" and m2.agendada_para >= volta  # segue aprovada, mas só depois do retorno
    schedule_approved(db, ws.id, NOW + timedelta(days=6), random.Random(4))
    db.commit()
    assert m2.agendada_para >= volta + timedelta(days=1) - timedelta(hours=12)


def test_descadastro_por_resposta_suprime_na_hora(db, world):
    ws = world.a
    ct, m1, m2, m3 = _enviada(db, ws)
    ingest_raw(db, ws.id, reply_raw(ct.email, m1.message_id, "Por favor remova meu email, não quero receber."))
    db.commit()
    assert db.scalar(select(Suppression.origem).where(Suppression.email_ou_dominio == ct.email)) == "descadastro_resposta"
    db.refresh(ct)
    assert ct.bloqueado_envio and m2.status == "cancelada"


def test_objecao_registra_aprendizado(db, world):
    ws = world.a
    ct, m1, m2, m3 = _enviada(db, ws)
    ingest_raw(db, ws.id, reply_raw(ct.email, m1.message_id, "Já temos fornecedor fixo, obrigado."))
    d = db.scalar(select(Deal))
    assert d.estagio == "nutricao" and "fornecedor" in d.aprendizado


def test_resposta_de_quem_nao_e_nosso_contato_e_ignorada_e_dsn_vira_bounce(db, world):
    ws = world.a
    ct, m1, m2, m3 = _enviada(db, ws)
    assert ingest_raw(db, ws.id, reply_raw("estranho@outro.com", "<nada@x>", "oi"))["tipo"] == "ignorado"
    m1.message_id = "<abc123@viaenvios.com.br>"
    db.commit()
    r = ingest_raw(db, ws.id, DSN_HARD)
    assert r == {"tipo": "bounce", "resultado": "invalido"}


def test_resposta_casa_pelo_remetente_quando_o_cabecalho_some(db, world):
    ws = world.a
    ct, m1, m2, m3 = _enviada(db, ws)
    r = ingest_raw(db, ws.id, reply_raw(ct.email, None, "Tenho interesse, vamos agendar", mid="<sem-header@x>"))
    assert r["classe"] == "interessado"


# ======================= telas =======================
def test_painel_de_respostas_negocios_tarefas_e_funil(logged, db, world):
    ws = world.a
    ct, m1, m2, m3 = _enviada(db, ws)
    ingest_raw(db, ws.id, reply_raw(ct.email, m1.message_id, "Tenho interesse! Podemos conversar?"))
    db.commit()
    html = logged.get("/disparo/respostas").text
    assert "interessado" in html and "WhatsApp/ligação" in html and "CRM: sem destino" in html
    d = db.scalar(select(Deal))
    from tests.conftest import token
    tk = token(logged, "/disparo/respostas")
    r = logged.post(f"/disparo/negocios/{d.id}/estagio", data={"csrf_token": tk, "estagio": "reuniao", "reuniao": "2026-11-20"})
    assert r.status_code == 303
    db.refresh(d)
    assert d.estagio == "reuniao" and d.reuniao_em.date().isoformat() == "2026-11-20"
    assert logged.post(f"/disparo/negocios/{d.id}/estagio", data={"csrf_token": tk, "estagio": "xyz"}).status_code == 422
    t = db.scalar(select(Task))
    assert logged.post(f"/disparo/tarefas/{t.id}/feita", data={"csrf_token": tk}).status_code == 303
    db.refresh(t)
    assert t.feita
    csv_txt = logged.get("/disparo/funil.csv").text
    assert csv_txt.startswith("empresa,contato") and "reuniao" in csv_txt and ct.email in csv_txt


def test_respostas_isoladas_por_workspace(client, db, world):
    from tests.conftest import login, token
    ct, m1, m2, m3 = _enviada(db, world.b)
    ingest_raw(db, world.b.id, reply_raw(ct.email, m1.message_id, "Tenho interesse!"))
    db.commit()
    d = db.scalar(select(Deal))
    login(client, world.user_a.email)
    assert ct.email not in client.get("/disparo/respostas").text
    assert ct.email not in client.get("/disparo/funil.csv").text
    tk = token(client, "/disparo/respostas")
    assert client.post(f"/disparo/negocios/{d.id}/estagio", data={"csrf_token": tk, "estagio": "ganho"}).status_code == 404
    assert db.scalar(select(func.count()).select_from(InboundReply)) == 1
