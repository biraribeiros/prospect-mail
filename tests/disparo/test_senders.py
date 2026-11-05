"""Etapas 6a e 6b: caixas/domínios, DNS, trava do domínio principal, aquecimento, agenda, cabeçalhos, bounces e disjuntor."""
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.core.models import (ComplianceRecord, Contact, MailboxAccount, OutboundMessage, SenderDomain, Suppression, Workspace)
from app.disparo import breaker
from app.disparo.bounces import ingest_dsn, parse_dsn, register_bounce
from app.disparo.dnscheck import check_dkim, check_dmarc, check_spf, preflight
from app.disparo.guard import (DomainNotReady, MainDomainError, SendBlocked, SendingPaused, assert_can_send, is_main_domain,
                               main_domains, register_sender_domain)
from app.disparo.mime import build_email, make_unsub_token, read_unsub_token
from app.disparo.scheduler import (day_bounds, in_window, next_window_start, recipient_tz, schedule_approved, send_due, warmup_limit)
from app.disparo.unsubscribe import suppress
from tests.conftest import login, token
from tests.disparo.helpers import NOW, FakeDns, FakeSender, contato, dns_ok, hunter_box, mensagem, sender_box


# ======================= 6a: DNS =======================
def test_spf():
    assert check_spf(["v=spf1 include:_spf.google.com ~all"])[0]
    assert check_spf(["v=spf1 ip4:1.2.3.4 -all"])[0]
    for ruim in (["v=spf1 +all"], ["v=spf1 ?all"], ["v=spf1 include:x.com"], [], ["v=spf1 -all"],
                 ["v=spf1 include:a.com ~all", "v=spf1 include:b.com ~all"]):
        assert not check_spf(ruim)[0]


def test_dkim_e_dmarc():
    assert check_dkim(["v=DKIM1; k=rsa; p=" + "B" * 100])[0]
    assert not check_dkim(["v=DKIM1; k=rsa; p="])[0] and not check_dkim([])[0]
    assert check_dmarc(["v=DMARC1; p=reject"])[0] and check_dmarc(["v=DMARC1;p=quarantine"])[0]
    assert not check_dmarc(["v=DMARC1; p=none"])[0] and not check_dmarc([])[0]


def test_preflight_completo_e_falhas():
    rep = preflight("envio-agencia.com.br", "default", "mail.envio-agencia.com.br", dns_ok())
    assert rep.pronto and rep.problemas == []
    d = dns_ok()
    d._txt.pop("_dmarc.envio-agencia.com.br")
    rep = preflight("envio-agencia.com.br", "default", "mail.envio-agencia.com.br", d)
    assert not rep.pronto and not rep.dmarc_ok and rep.spf_ok and "DMARC ausente" in rep.problemas
    d2 = dns_ok()
    d2._ptr = {}
    assert not preflight("envio-agencia.com.br", "default", "mail.envio-agencia.com.br", d2).rdns_ok
    d3 = dns_ok()
    d3._ptr = {"203.0.113.10": ["outro.nome.com"]}  # PTR que não aponta de volta para o IP
    assert not preflight("envio-agencia.com.br", "default", "mail.envio-agencia.com.br", d3).rdns_ok
    assert not preflight("envio-agencia.com.br", None, "mail.envio-agencia.com.br", dns_ok()).dkim_ok


# ======================= 6a: guard =======================
def test_dominio_sem_dmarc_adequado_bloqueia_envio(db, world):
    mb, d = sender_box(db, world.a, pronto=True)
    d.dmarc_ok = False
    db.commit()
    with pytest.raises(DomainNotReady) as e:
        assert_can_send(db, mb)
    assert "DMARC" in str(e.value)
    ct, c = contato(db, world.a, "x@cliente.com.br")
    m = mensagem(db, world.a, ct, c, status="agendada", mailbox_id=mb.id, agendada_para=NOW)
    out = send_due(db, world.a.id, FakeSender(), NOW)
    assert out["bloqueadas"] == 1 and m.status == "bloqueada" and "DMARC" in m.motivo


def test_tentar_enviar_pelo_dominio_principal_levanta_excecao(db, world):
    hunter_box(db, world.a, "agenciavia.com.br")
    mb, d = sender_box(db, world.a, dominio="agenciavia.com.br")  # alguém cadastrou o principal como "secundário"
    with pytest.raises(MainDomainError):
        assert_can_send(db, mb)
    assert is_main_domain(db, world.a.id, "AgenciaVia.com.br.")


def test_registro_de_dominio_secundario_igual_ou_subdominio_do_principal_e_recusado(db, world):
    hunter_box(db, world.a, "agenciavia.com.br")
    for ruim in ("agenciavia.com.br", "AGENCIAVIA.COM.BR", "www.agenciavia.com.br", "mail.agenciavia.com.br", "agenciavia.com.br."):
        with pytest.raises(MainDomainError):
            register_sender_domain(db, world.a.id, ruim, "secundario")
        db.rollback()
    ok = register_sender_domain(db, world.a.id, "viaenvios.com.br", "secundario")
    assert ok.nome == "viaenvios.com.br"
    with pytest.raises(ValueError):
        register_sender_domain(db, world.a.id, "semponto", "secundario")


def test_principal_e_derivado_do_endereco_das_caixas_lidas(db, world):
    from app.core.security import encrypt_json
    db.add(MailboxAccount(workspace_id=world.a.id, tipo="mbox", nome="x", uso="hunter", dominio=None,
                          tokens_enc=encrypt_json({"path": "p", "emails": ["bira@dominiolido.com.br"]})))
    db.commit()
    assert "dominiolido.com.br" in main_domains(db, world.a.id)
    with pytest.raises(MainDomainError):
        register_sender_domain(db, world.a.id, "dominiolido.com.br", "secundario")


def test_caixa_lida_depois_do_cadastro_ainda_bloqueia_no_envio(db, world):
    mb, d = sender_box(db, world.a, dominio="viaenvios.com.br")
    assert assert_can_send(db, mb).nome == "viaenvios.com.br"  # hoje está liberado
    hunter_box(db, world.a, "viaenvios.com.br")  # depois o Hunter passa a ler esse domínio
    with pytest.raises(MainDomainError):
        assert_can_send(db, mb)


def test_tipo_principal_nunca_envia_e_tipo_nao_muda_depois_do_primeiro_envio(db, world):
    mb, d = sender_box(db, world.a, tipo_dom="principal")
    with pytest.raises(MainDomainError):
        assert_can_send(db, mb)
    mb2, d2 = sender_box(db, world.a, dominio="viaenvios.com.br")
    ct, c = contato(db, world.a, "x@cliente.com.br")
    mensagem(db, world.a, ct, c, status="agendada", mailbox_id=mb2.id, agendada_para=NOW)
    assert send_due(db, world.a.id, FakeSender(), NOW)["enviadas"] == 1
    db.refresh(d2)
    assert d2.ja_enviou
    d2.tipo = "principal"  # tentativa de mudar o tipo depois de enviar
    with pytest.raises(MainDomainError):
        db.flush()
    db.rollback()
    assert db.get(SenderDomain, d2.id).tipo == "secundario"


def test_caixa_de_leitura_e_mbox_nao_enviam(db, world):
    hb = hunter_box(db, world.a)
    with pytest.raises(SendBlocked):
        assert_can_send(db, hb)
    mb, _ = sender_box(db, world.a, dominio="viaenvios.com.br")
    mb.pausada = True
    db.commit()
    with pytest.raises(SendBlocked):
        assert_can_send(db, mb)


# ======================= 6a: aquecimento =======================
def test_aquecimento_5_15_30_50_por_semana_e_limite_manual_so_reduz(db, world):
    mb, _ = sender_box(db, world.a, dominio="viaenvios.com.br")
    assert warmup_limit(db, mb, NOW) == 5  # sem envios: semana 1
    ct, c = contato(db, world.a, "a@x.com")
    antigo = mensagem(db, world.a, ct, c, status="enviada", mailbox_id=mb.id, enviada_em=NOW - timedelta(days=1))
    for dias, esperado in ((6, 5), (7, 15), (14, 30), (21, 50), (100, 50)):
        antigo.enviada_em = NOW - timedelta(days=dias)
        db.commit()
        assert warmup_limit(db, mb, NOW) == esperado, dias
    mb.limite_diario = 20
    db.commit()
    assert warmup_limit(db, mb, NOW) == 20  # manual reduz
    mb.limite_diario = 500
    db.commit()
    assert warmup_limit(db, mb, NOW) == 50  # manual não passa do plano


# ======================= 6b: janela e fuso =======================
def test_janela_comercial_no_fuso_do_destinatario(db, world):
    _, c_sp = contato(db, world.a, "a@x.com", uf="SP")
    _, c_am = contato(db, world.a, "b@x.com", uf="AM")
    sp, am = recipient_tz(c_sp), recipient_tz(c_am)
    assert str(sp) == "America/Sao_Paulo" and str(am) == "America/Manaus"
    assert in_window(NOW, sp) and in_window(NOW, am)  # 10h SP / 9h AM
    cedo = datetime(2026, 11, 9, 11, 30, tzinfo=timezone.utc)  # 8h30 SP, 7h30 AM
    assert in_window(cedo, sp) and not in_window(cedo, am)
    noite = datetime(2026, 11, 9, 21, 30, tzinfo=timezone.utc)  # 18h30 SP
    assert not in_window(noite, sp)
    sabado = datetime(2026, 11, 14, 14, 0, tzinfo=timezone.utc)
    assert not in_window(sabado, sp)
    assert next_window_start(sabado, sp) == datetime(2026, 11, 16, 11, 0, tzinfo=timezone.utc)  # segunda 8h SP
    assert next_window_start(noite, sp) == datetime(2026, 11, 10, 11, 0, tzinfo=timezone.utc)  # terça 8h SP
    assert next_window_start(NOW, sp) == NOW


# ======================= 6b: agenda =======================
def _caixas_prontas(db, ws, n, antigas=True):
    boxes = []
    for i in range(n):
        mb, _ = sender_box(db, ws, dominio=f"envio{i}.com.br")
        boxes.append(mb)
    if antigas:  # já aquecidas: 50/dia
        ct, c = contato(db, ws, "historico@x.com")
        for mb in boxes:
            mensagem(db, ws, ct, c, status="enviada", mailbox_id=mb.id, enviada_em=NOW - timedelta(days=60))
    return boxes


def test_200_envios_respeitam_intervalo_janela_e_limites(db, world):
    ws = world.a
    boxes = _caixas_prontas(db, ws, 4)
    for i in range(200):
        ct, c = contato(db, ws, f"p{i}@cliente{i}.com.br", uf="SP" if i % 5 else "AM")
        mensagem(db, ws, ct, c)
    res = schedule_approved(db, ws.id, NOW, random.Random(7))
    db.commit()
    assert res["agendadas"] == 200 and res["adiadas_por_limite"] == 0
    msgs = db.scalars(select(OutboundMessage).where(OutboundMessage.status == "agendada").order_by(OutboundMessage.agendada_para)).all()
    por_caixa: dict[int, list] = {}
    for m in msgs:
        por_caixa.setdefault(m.mailbox_id, []).append(m)
        comp = db.get(__import__("app.core.models", fromlist=["Company"]).Company, m.company_id)
        assert in_window(m.agendada_para, recipient_tz(comp)), m.agendada_para
        assert m.agendada_para >= NOW
    assert len(por_caixa) == 4  # rodízio usa todas as caixas
    assert max(len(v) for v in por_caixa.values()) - min(len(v) for v in por_caixa.values()) <= 8  # distribuição equilibrada
    for mid, lst in por_caixa.items():
        dias: dict = {}
        for m in lst:
            dias.setdefault(day_bounds(m.agendada_para)[0], []).append(m)
        assert all(len(v) <= 50 for v in dias.values())  # limite diário (aquecimento concluído)
        lst.sort(key=lambda m: m.agendada_para)
        for a, b in zip(lst, lst[1:]):
            assert (b.agendada_para - a.agendada_para) >= timedelta(minutes=3)  # intervalo mínimo na mesma caixa


def test_aquecimento_limita_a_5_por_dia_na_primeira_semana(db, world):
    ws = world.a
    mb, _ = sender_box(db, ws, dominio="novo.com.br")
    for i in range(12):
        ct, c = contato(db, ws, f"p{i}@c{i}.com.br")
        mensagem(db, ws, ct, c)
    schedule_approved(db, ws.id, NOW, random.Random(1))
    db.commit()
    dias: dict = {}
    for m in db.scalars(select(OutboundMessage).where(OutboundMessage.status == "agendada")):
        dias.setdefault(day_bounds(m.agendada_para)[0], 0)
        dias[day_bounds(m.agendada_para)[0]] += 1
    assert max(dias.values()) <= 5 and len(dias) >= 3  # 12 mensagens espalhadas em ao menos 3 dias úteis


def test_sem_caixa_pronta_nao_agenda(db, world):
    sender_box(db, world.a, dominio="semdns.com.br", pronto=False)
    ct, c = contato(db, world.a, "a@x.com")
    mensagem(db, world.a, ct, c)
    res = schedule_approved(db, world.a.id, NOW)
    assert res["agendadas"] == 0 and res["sem_caixa"] == 1


# ======================= 6b: envio =======================
def test_envio_com_cabecalhos_obrigatorios(db, world):
    ws = world.a
    mb, d = sender_box(db, ws, dominio="viaenvios.com.br")
    ct, c = contato(db, ws, "ana@cliente.com.br")
    m = mensagem(db, ws, ct, c, status="agendada", mailbox_id=mb.id, agendada_para=NOW)
    s = FakeSender()
    out = send_due(db, ws.id, s, NOW)
    assert out["enviadas"] == 1 and m.status == "enviada" and m.enviada_em == NOW
    msg = s.enviadas[0][1]
    assert msg["Message-ID"] == m.message_id and m.message_id.endswith("@viaenvios.com.br>")
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert "/u/" in msg["List-Unsubscribe"] and "mailto:contato@viaenvios.com.br" in msg["List-Unsubscribe"]
    assert msg.get_content_type() == "text/plain" and "/u/" in msg.get_content() and msg["To"] == "ana@cliente.com.br"
    assert not msg.is_multipart()
    db.refresh(d)
    assert d.ja_enviou


def test_followup_fica_na_mesma_conversa(db, world):
    ws = world.a
    mb, _ = sender_box(db, ws, dominio="viaenvios.com.br")
    ct, c = contato(db, ws, "ana@cliente.com.br")
    m1 = mensagem(db, ws, ct, c, status="agendada", mailbox_id=mb.id, agendada_para=NOW)
    s = FakeSender()
    send_due(db, ws.id, s, NOW)
    m2 = mensagem(db, ws, ct, c, status="agendada", mailbox_id=mb.id, agendada_para=NOW + timedelta(days=4), passo=2,
                  thread_raiz_id=m1.id, assunto="Re: Uma ideia")
    send_due(db, ws.id, s, NOW + timedelta(days=4))
    msg2 = s.enviadas[1][1]
    assert msg2["In-Reply-To"] == m1.message_id and m1.message_id in msg2["References"]


def test_supressao_e_conferida_a_cada_mensagem(db, world):
    ws = world.a
    mb, _ = sender_box(db, ws, dominio="viaenvios.com.br")
    a, ca = contato(db, ws, "a@cliente.com.br")
    b, cb = contato(db, ws, "b@concorrente.com")
    c_, cc = contato(db, ws, "c@ok.com.br")
    for ct, co in ((a, ca), (b, cb), (c_, cc)):
        mensagem(db, ws, ct, co, status="agendada", mailbox_id=mb.id, agendada_para=NOW)
    db.add_all([Suppression(workspace_id=ws.id, email_ou_dominio="a@cliente.com.br", motivo="x", origem="manual"),
                Suppression(workspace_id=ws.id, email_ou_dominio="concorrente.com", motivo="x", origem="manual")])
    db.commit()
    s = FakeSender()
    out = send_due(db, ws.id, s, NOW)
    assert out["enviadas"] == 1 and out["bloqueadas"] == 2 and s.enviadas[0][1]["To"] == "c@ok.com.br"


def test_contato_invalido_ou_bloqueado_nao_recebe(db, world):
    ws = world.a
    mb, _ = sender_box(db, ws, dominio="viaenvios.com.br")
    a, ca = contato(db, ws, "a@x.com", status="invalido")
    b, cb = contato(db, ws, "b@x.com", bloqueado_envio=True, bloqueio_motivo="contador")
    for ct, co in ((a, ca), (b, cb)):
        mensagem(db, ws, ct, co, status="agendada", mailbox_id=mb.id, agendada_para=NOW)
    out = send_due(db, ws.id, FakeSender(), NOW)
    assert out["enviadas"] == 0 and out["bloqueadas"] == 2


def test_falha_de_smtp_tenta_de_novo_ate_3_vezes(db, world):
    ws = world.a
    mb, _ = sender_box(db, ws, dominio="viaenvios.com.br")
    ct, c = contato(db, ws, "a@x.com")
    m = mensagem(db, ws, ct, c, status="agendada", mailbox_id=mb.id, agendada_para=NOW)
    s = FakeSender(falhar={"a@x.com"})
    t = NOW
    for i in range(3):
        send_due(db, ws.id, s, t)
        t = m.agendada_para + timedelta(minutes=1) if m.status == "agendada" else t
    assert m.status == "falhou" and m.tentativas == 3


def test_nao_passa_do_limite_diario_no_envio(db, world):
    ws = world.a
    mb, _ = sender_box(db, ws, dominio="viaenvios.com.br")
    for i in range(8):
        ct, c = contato(db, ws, f"p{i}@c.com")
        mensagem(db, ws, ct, c, status="agendada", mailbox_id=mb.id, agendada_para=NOW)  # alguém forçou 8 no mesmo dia
    out = send_due(db, ws.id, FakeSender(), NOW)
    assert out["enviadas"] == 5  # semana 1: 5 por dia; o resto fica para depois


# ======================= 6b: bounces =======================
DSN_HARD = b"""From: MAILER-DAEMON@mx.x.com
To: contato@viaenvios.com.br
Subject: Delivery Status Notification (Failure)
MIME-Version: 1.0
Content-Type: multipart/report; report-type=delivery-status; boundary="B"

--B
Content-Type: text/plain

Falha.
--B
Content-Type: message/delivery-status

Reporting-MTA: dns; mx.x.com

Final-Recipient: rfc822; ana@cliente.com.br
Action: failed
Status: 5.1.1

--B
Content-Type: message/rfc822

Message-ID: <abc123@viaenvios.com.br>
From: contato@viaenvios.com.br
To: ana@cliente.com.br
Subject: Uma ideia

corpo
--B--
"""


def test_parse_dsn():
    assert parse_dsn(DSN_HARD) == ("<abc123@viaenvios.com.br>", "hard")
    soft = DSN_HARD.replace(b"Action: failed\nStatus: 5.1.1", b"Action: delayed\nStatus: 4.2.2")
    assert parse_dsn(soft)[1] == "soft"
    assert parse_dsn(b"Subject: oi\n\nnada") == (None, None)


def _enviada(db, ws, email="ana@cliente.com.br", mid="<abc123@viaenvios.com.br>"):
    mb, _ = sender_box(db, ws, dominio="viaenvios.com.br")
    ct, c = contato(db, ws, email)
    m = mensagem(db, ws, ct, c, status="enviada", mailbox_id=mb.id, enviada_em=NOW, message_id=mid)
    return ct, m


def test_hard_bounce_invalida_suprime_e_cancela_pendentes(db, world):
    ws = world.a
    ct, m = _enviada(db, ws)
    pend = mensagem(db, ws, ct, status="agendada", agendada_para=NOW + timedelta(days=4), passo=2)
    assert ingest_dsn(db, ws.id, DSN_HARD) == "invalido"
    db.commit()
    db.refresh(ct)
    assert ct.email_status == "invalido" and m.bounce_tipo == "hard" and pend.status == "cancelada"
    assert db.scalar(select(Suppression.motivo).where(Suppression.email_ou_dominio == "ana@cliente.com.br")) == "bounce permanente"


def test_soft_bounce_tenta_de_novo_e_na_segunda_vira_arriscado(db, world):
    ws = world.a
    ct, m = _enviada(db, ws)
    assert register_bounce(db, ws.id, m.message_id, "soft", NOW) == "reagendada"
    retry = db.scalar(select(OutboundMessage).where(OutboundMessage.status == "aprovada"))
    assert retry is not None and retry.agendada_para > NOW
    retry.status, retry.message_id, retry.mailbox_id = "enviada", "<seg@viaenvios.com.br>", m.mailbox_id
    retry.enviada_em = NOW + timedelta(hours=7)
    db.commit()
    assert register_bounce(db, ws.id, "<seg@viaenvios.com.br>", "soft", NOW + timedelta(hours=8)) == "arriscado"
    db.refresh(ct)
    assert ct.email_status == "arriscado"
    assert register_bounce(db, ws.id, "<inexistente@x>", "hard") == "ignorado"


# ======================= 6b: disjuntor =======================
def test_bounce_acima_de_3_por_cento_pausa_tudo_e_avisa(db, world):
    ws = world.a
    mb, _ = sender_box(db, ws, dominio="viaenvios.com.br")
    antiga, _ = sender_box(db, ws, dominio="outro-envio.com.br")  # as 100 enviadas saíram por outra caixa
    msgs = []
    for i in range(100):
        ct, c = contato(db, ws, f"p{i}@c{i}.com")
        msgs.append(mensagem(db, ws, ct, c, status="enviada", mailbox_id=antiga.id, enviada_em=NOW - timedelta(hours=2), message_id=f"<m{i}@v>"))
    alertas = []
    for i in range(3):  # 3% exatamente: ainda não dispara (precisa passar de 3%)
        register_bounce(db, ws.id, f"<m{i}@v>", "hard", NOW)
    assert not breaker.is_paused(db, ws.id)
    msgs[3].bounce_tipo, msgs[3].bounce_em = "hard", NOW
    db.flush()
    assert breaker.evaluate(db, ws.id, NOW, alertas.append) is True  # 4% > 3%
    assert breaker.is_paused(db, ws.id) and "4 de 100" in breaker.pause_info(db, ws.id)["motivo"] and len(alertas) == 1
    assert breaker.evaluate(db, ws.id, NOW, alertas.append) is False  # não dispara duas vezes
    with pytest.raises(SendingPaused):
        assert_can_send(db, mb)
    ct, c = contato(db, ws, "novo@cliente.com")
    m = mensagem(db, ws, ct, c, status="agendada", mailbox_id=mb.id, agendada_para=NOW)
    s = FakeSender()
    out = send_due(db, ws.id, s, NOW)
    assert out["pausado"] and s.enviadas == [] and m.status == "agendada"  # nada sai, nada se perde
    breaker.resume(db, ws.id)
    assert send_due(db, ws.id, s, NOW)["enviadas"] == 1


def test_amostra_pequena_nao_dispara_o_disjuntor(db, world):
    ws = world.a
    ct, m = _enviada(db, ws)
    register_bounce(db, ws.id, m.message_id, "hard", NOW)  # 1 de 1 = 100%, mas amostra < 20
    assert not breaker.is_paused(db, ws.id)


# ======================= descadastro =======================
def test_token_de_descadastro_e_assinado():
    t = make_unsub_token(5, "Ana@X.com")
    assert read_unsub_token(t) == (5, "ana@x.com")
    assert read_unsub_token(t + "x") is None and read_unsub_token("lixo") is None


def test_descadastro_pelo_link_suprime_cancela_e_registra(client, db, world):
    ws = world.a
    mb, _ = sender_box(db, ws, dominio="viaenvios.com.br")
    ct, c = contato(db, ws, "ana@cliente.com.br")
    db.add(ComplianceRecord(workspace_id=ws.id, entidade_tipo="contact", entidade_id=ct.id, origem_dado="site", finalidade="x", base_legal="y"))
    pend = mensagem(db, ws, ct, c, status="agendada", mailbox_id=mb.id, agendada_para=NOW)
    db.commit()
    t = make_unsub_token(ws.id, "ana@cliente.com.br")
    page = client.get(f"/u/{t}")
    assert page.status_code == 200 and "ana@cliente.com.br" in page.text
    assert db.scalar(select(func.count()).select_from(Suppression)) == 0  # GET não descadastra (robôs abrem links)
    r = client.post(f"/u/{t}", content="List-Unsubscribe=One-Click", headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200 and "Pronto" in r.text  # sem CSRF: é chamada do provedor de email
    db.expire_all()
    assert db.scalar(select(Suppression.origem).where(Suppression.email_ou_dominio == "ana@cliente.com.br")) == "descadastro_link"
    assert pend.status == "cancelada" and db.get(Contact, ct.id).bloqueado_envio
    rec = db.scalar(select(ComplianceRecord))
    assert rec.historico_oposicao and rec.historico_oposicao[0]["origem"] == "descadastro_link"
    assert client.get("/u/lixo").status_code == 404 and client.post("/u/lixo").status_code == 404
    assert suppress(db, ws.id, "ana@cliente.com.br") == 1  # repetir não duplica
    assert db.scalar(select(func.count()).select_from(Suppression)) == 1


# ======================= telas =======================
def test_tela_de_disparo_e_cadastro_de_caixa(logged, db, world):
    hunter_box(db, world.a, "agenciavia.com.br")
    html = logged.get("/disparo").text
    assert "Nenhuma caixa de envio" in html and "agenciavia.com.br" in html
    tk = token(logged, "/disparo")
    base = {"csrf_token": tk, "host": "mail.viaenvios.com.br", "port": "587", "security": "starttls", "password": "segredo",
            "selector": "default", "limite": "0"}
    r = logged.post("/disparo/caixas", data={**base, "email": "contato@agenciavia.com.br"})
    assert r.status_code == 303 and db.scalar(select(func.count()).select_from(SenderDomain)) == 0  # principal recusado
    assert "domínio principal" in logged.get("/disparo").text
    r = logged.post("/disparo/caixas", data={**base, "email": "contato@viaenvios.com.br"})
    assert r.status_code == 303
    mb = db.scalar(select(MailboxAccount).where(MailboxAccount.tipo == "smtp"))
    assert mb.uso == "envio" and "segredo" not in mb.tokens_enc  # senha cifrada
    html = logged.get("/disparo").text
    assert "viaenvios.com.br" in html and "SPF ✘" in html
    r = logged.post("/disparo/caixas", data={**base, "email": "x@y.com", "host": "10.0.0.1"})
    assert r.status_code == 303 and db.scalar(select(func.count()).select_from(MailboxAccount).where(MailboxAccount.tipo == "smtp")) == 1


def test_disparo_isolamento_e_papeis(client, db, world):
    mb, d = sender_box(db, world.b, dominio="envio-b.com.br")
    login(client, world.user_a.email)
    tk = token(client, "/disparo")
    assert "envio-b.com.br" not in client.get("/disparo").text
    assert client.post(f"/disparo/caixas/{mb.id}/pausar", data={"csrf_token": tk}).status_code == 404
    assert client.post(f"/disparo/dominios/{d.id}/checar", data={"csrf_token": tk}).status_code == 404
    client.post("/logout", data={"csrf_token": tk})
    login(client, world.approver_a.email)
    tk = token(client, "/disparo")
    assert client.post("/disparo/caixas", data={"csrf_token": tk, "email": "a@b.com"}).status_code == 403
    assert client.post("/disparo/liberar", data={"csrf_token": tk}).status_code == 403
