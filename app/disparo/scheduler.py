"""Etapa 6b: agendador (rodízio de caixas, intervalo aleatório 3–15 min, janela comercial do destinatário, aquecimento)
e envio das mensagens vencidas, conferindo a lista de supressão e todas as travas a CADA mensagem."""
import random
import smtplib
import ssl
from collections.abc import Callable
from datetime import datetime, time, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.models import Company, Contact, MailboxAccount, OutboundMessage, SenderDomain, Suppression
from app.core.security import decrypt_json
from app.disparo import breaker
from app.disparo.guard import SendBlocked, assert_can_send, domain_of_address, norm_domain
from app.disparo.mime import build_email, new_message_id

TZ_UF = {"AC": "America/Rio_Branco", "AM": "America/Manaus", "RO": "America/Porto_Velho", "RR": "America/Boa_Vista",
         "MT": "America/Cuiaba", "MS": "America/Campo_Grande"}
TZ_PADRAO = "America/Sao_Paulo"
JANELA = (time(8, 0), time(18, 0))
INTERVALO_MIN, INTERVALO_MAX = 3, 15  # minutos
MAX_TENTATIVAS = 3
FASES_AQUECIMENTO = (5, 15, 30, 50)  # envios por dia nas semanas 1 a 4 (depois da 4ª semana: 50)


class Sender(Protocol):
    def send(self, mailbox: MailboxAccount, cfg: dict, msg) -> None: ...


class SmtpSender:
    """Em desenvolvimento/DRY_RUN tudo vai para o Mailpit; o SMTP real só é usado com DRY_RUN=false."""

    def send(self, mailbox: MailboxAccount, cfg: dict, msg) -> None:
        s = get_settings()
        if s.dry_run:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as c:
                c.send_message(msg)
            return
        host, port, sec = cfg["host"], int(cfg.get("port", 587)), cfg.get("security", "starttls")
        ctx = ssl.create_default_context()
        if sec == "ssl":
            c = smtplib.SMTP_SSL(host, port, context=ctx, timeout=30)
        else:
            c = smtplib.SMTP(host, port, timeout=30)
        with c:
            if sec == "starttls":
                c.starttls(context=ctx)
            c.login(cfg["user"], cfg["password"])
            c.send_message(msg)


# ---------- janela e aquecimento ----------
def recipient_tz(company: Company | None) -> ZoneInfo:
    return ZoneInfo(TZ_UF.get((company.uf or "").upper(), TZ_PADRAO) if company else TZ_PADRAO)


def in_window(when: datetime, tz: ZoneInfo) -> bool:
    local = when.astimezone(tz)
    return local.weekday() < 5 and JANELA[0] <= local.time() < JANELA[1]


def next_window_start(when: datetime, tz: ZoneInfo) -> datetime:
    """Primeiro instante dentro da janela comercial a partir de 'when' (seg–sex, 8h–18h, hora do destinatário)."""
    local = when.astimezone(tz)
    if local.weekday() < 5 and JANELA[0] <= local.time() < JANELA[1]:
        return when
    day = local.date()
    if local.weekday() < 5 and local.time() < JANELA[0]:
        pass  # hoje, às 8h
    else:
        day += timedelta(days=1)
    while datetime.combine(day, JANELA[0]).weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, JANELA[0], tzinfo=tz).astimezone(timezone.utc)


def warmup_limit(session: Session, mailbox: MailboxAccount, now: datetime) -> int:
    """Limite diário da caixa: plano 5/15/30/50 pelas semanas desde o 1º envio; o limite manual só pode REDUZIR."""
    first = session.scalar(select(func.min(OutboundMessage.enviada_em)).where(OutboundMessage.mailbox_id == mailbox.id))
    semana = 0 if first is None else min(3, max(0, (now - first).days // 7))
    plano = FASES_AQUECIMENTO[semana]
    return min(plano, mailbox.limite_diario) if mailbox.limite_diario and mailbox.limite_diario > 0 else plano


def day_bounds(now: datetime, tz: ZoneInfo = ZoneInfo(TZ_PADRAO)) -> tuple[datetime, datetime]:
    local = now.astimezone(tz)
    start = datetime.combine(local.date(), time(0, 0), tzinfo=tz)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def used_today(session: Session, mailbox_id: int, now: datetime, only_sent: bool = False) -> int:
    a, b = day_bounds(now)
    status = ("enviada",) if only_sent else ("enviada", "agendada")
    return session.scalar(select(func.count()).select_from(OutboundMessage).where(
        OutboundMessage.mailbox_id == mailbox_id, OutboundMessage.status.in_(status),
        func.coalesce(OutboundMessage.enviada_em, OutboundMessage.agendada_para) >= a,
        func.coalesce(OutboundMessage.enviada_em, OutboundMessage.agendada_para) < b)) or 0


def ready_mailboxes(session: Session, ws: int) -> list[MailboxAccount]:
    out = []
    for mb in session.scalars(select(MailboxAccount).where(MailboxAccount.workspace_id == ws, MailboxAccount.uso.in_(("envio", "ambos")))
                              .order_by(MailboxAccount.id)):
        try:
            assert_can_send(session, mb)
            out.append(mb)
        except SendBlocked:
            continue
    return out


# ---------- agendar ----------
def schedule_approved(session: Session, ws: int, now: datetime | None = None, rng: random.Random | None = None) -> dict:
    """Pega mensagens 'aprovada' e marca data/hora e caixa: rodízio entre caixas prontas, 3–15 min entre envios da mesma caixa,
    dentro da janela comercial do destinatário e do limite diário (aquecimento)."""
    now = now or datetime.now(timezone.utc)
    rng = rng or random.Random()
    boxes = ready_mailboxes(session, ws)
    out = {"agendadas": 0, "sem_caixa": 0, "adiadas_por_limite": 0}
    if not boxes:
        out["sem_caixa"] = session.scalar(select(func.count()).select_from(OutboundMessage).where(
            OutboundMessage.workspace_id == ws, OutboundMessage.status == "aprovada")) or 0
        return out
    # cursor por caixa: quando ela fica livre de novo (último envio/agendamento + intervalo)
    livre: dict[int, datetime] = {}
    for mb in boxes:
        last = session.scalar(select(func.max(func.coalesce(OutboundMessage.enviada_em, OutboundMessage.agendada_para))).where(
            OutboundMessage.mailbox_id == mb.id, OutboundMessage.status.in_(("enviada", "agendada"))))
        livre[mb.id] = max(now, last + timedelta(minutes=rng.randint(INTERVALO_MIN, INTERVALO_MAX))) if last else now
    contagem: dict[tuple[int, str], int] = {}
    pend = session.scalars(select(OutboundMessage).where(OutboundMessage.workspace_id == ws, OutboundMessage.status == "aprovada")
                           .order_by(OutboundMessage.passo, OutboundMessage.id)).all()
    rr = 0

    def slot(mb: MailboxAccount, msg: OutboundMessage, tz: ZoneInfo) -> datetime | None:
        """Primeiro horário livre desta caixa para esta mensagem (janela comercial + limite diário), ou None."""
        t = next_window_start(max(livre[mb.id], msg.agendada_para or now), tz)
        for _ in range(14):  # até 2 semanas à frente
            a, _b = day_bounds(t)
            key = (mb.id, a.isoformat())
            if key not in contagem:
                contagem[key] = used_today(session, mb.id, t)
            if contagem[key] < warmup_limit(session, mb, t):
                return t
            t = next_window_start(a + timedelta(days=1, hours=11), tz)
        return None

    for msg in pend:
        company = session.get(Company, msg.company_id) if msg.company_id else None
        tz = recipient_tz(company)
        ordem = boxes[rr:] + boxes[:rr]  # rodízio: empate de horário vai para a caixa seguinte à última usada
        cand = [(t, i, mb) for i, mb in enumerate(ordem) if (t := slot(mb, msg, tz)) is not None]
        if not cand:
            out["adiadas_por_limite"] += 1
            continue
        t, _, mb = min(cand, key=lambda c: (c[0], c[1]))
        contagem[(mb.id, day_bounds(t)[0].isoformat())] += 1
        msg.mailbox_id, msg.agendada_para, msg.status = mb.id, t, "agendada"
        livre[mb.id] = t + timedelta(minutes=rng.randint(INTERVALO_MIN, INTERVALO_MAX))
        rr = (boxes.index(mb) + 1) % len(boxes)
        out["agendadas"] += 1
    session.flush()
    return out


# ---------- enviar ----------
def _suppressed(session: Session, ws: int, email: str) -> bool:
    dom = domain_of_address(email)
    return bool(session.scalar(select(Suppression.id).where(
        Suppression.workspace_id == ws, Suppression.email_ou_dominio.in_([email.lower(), dom]))))


def send_due(session: Session, ws: int, sender: Sender | None = None, now: datetime | None = None,
             alert: Callable[[str], None] | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    sender = sender or SmtpSender()
    out = {"enviadas": 0, "bloqueadas": 0, "falhas": 0, "pausado": False}
    due = session.scalars(select(OutboundMessage).where(
        OutboundMessage.workspace_id == ws, OutboundMessage.status == "agendada", OutboundMessage.agendada_para <= now)
        .order_by(OutboundMessage.agendada_para, OutboundMessage.id)).all()
    for msg in due:
        ct = session.get(Contact, msg.contact_id)
        mb = session.get(MailboxAccount, msg.mailbox_id) if msg.mailbox_id else None
        motivo = None
        if ct is None or not ct.email:
            motivo = "contato sem email"
        elif ct.bloqueado_envio:
            motivo = f"contato bloqueado ({ct.bloqueio_motivo or 'sem motivo'})"
        elif ct.email_status in ("invalido", "bloqueado"):
            motivo = f"email {ct.email_status}"
        elif _suppressed(session, ws, ct.email):
            motivo = "email ou domínio na lista de supressão"
        elif mb is None:
            motivo = "sem caixa de envio"
        if motivo:
            msg.status, msg.motivo = "bloqueada", motivo
            out["bloqueadas"] += 1
            continue
        try:
            dom = assert_can_send(session, mb)
        except SendBlocked as e:
            if breaker.is_paused(session, ws):
                out["pausado"] = True
                break  # disjuntor: nada sai, as mensagens continuam agendadas
            msg.status, msg.motivo = "bloqueada", str(e)
            out["bloqueadas"] += 1
            continue
        if used_today(session, mb.id, now, only_sent=True) >= warmup_limit(session, mb, now):
            msg.agendada_para = now + timedelta(hours=1)  # passou do limite do dia: tenta mais tarde
            continue
        cfg = decrypt_json(mb.tokens_enc) if mb.tokens_enc else {}
        mid = new_message_id(dom.nome)
        root = session.get(OutboundMessage, msg.thread_raiz_id) if msg.thread_raiz_id and msg.thread_raiz_id != msg.id else None
        email = build_email(from_addr=cfg.get("from") or cfg.get("user") or f"contato@{dom.nome}", from_name=cfg.get("from_name"),
                            to_addr=ct.email, subject=msg.assunto, body=msg.corpo, ws=ws, message_id=mid,
                            in_reply_to=root.message_id if root and root.message_id else None)
        try:
            sender.send(mb, cfg, email)
        except Exception as e:  # noqa: BLE001 - falha de SMTP: tenta de novo mais tarde, no máximo 3 vezes
            msg.tentativas += 1
            msg.motivo = f"falha no envio: {type(e).__name__}"
            if msg.tentativas >= MAX_TENTATIVAS:
                msg.status = "falhou"
            else:
                msg.agendada_para = now + timedelta(minutes=30 * msg.tentativas)
            out["falhas"] += 1
            continue
        msg.status, msg.message_id, msg.enviada_em = "enviada", mid, now
        msg.simulada = get_settings().dry_run
        if msg.thread_raiz_id is None:
            msg.thread_raiz_id = msg.id
        dom.ja_enviou = True  # a partir daqui o tipo do domínio não muda mais
        out["enviadas"] += 1
    session.flush()
    if out["enviadas"]:
        breaker.evaluate(session, ws, now, alert)
    return out


__all__ = ["schedule_approved", "send_due", "SmtpSender", "warmup_limit", "norm_domain", "SenderDomain"]
