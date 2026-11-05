"""Etapa 6b: disjuntor — se mais de 3% das mensagens das últimas 24 h voltaram (bounce), pausa TODO o envio do workspace,
avisa no painel e por email aos administradores. Só um administrador libera."""
import smtplib
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import get_settings
from app.core.models import AppUser, OutboundMessage, Workspace, WorkspaceMember


def is_paused(session: Session, ws: int) -> bool:
    return bool((session.get(Workspace, ws).config or {}).get("envio_pausado"))


def pause_info(session: Session, ws: int) -> dict | None:
    return (session.get(Workspace, ws).config or {}).get("envio_pausado")


def rates(session: Session, ws: int, now: datetime) -> tuple[int, int, float]:
    desde = now - timedelta(hours=24)
    sent = session.scalar(select(func.count()).select_from(OutboundMessage).where(
        OutboundMessage.workspace_id == ws, OutboundMessage.enviada_em >= desde)) or 0
    bounced = session.scalar(select(func.count()).select_from(OutboundMessage).where(
        OutboundMessage.workspace_id == ws, OutboundMessage.enviada_em >= desde, OutboundMessage.bounce_tipo.is_not(None))) or 0
    return sent, bounced, (bounced / sent if sent else 0.0)


def admin_emails(session: Session, ws: int) -> list[str]:
    return list(session.scalars(select(AppUser.email).join(WorkspaceMember, WorkspaceMember.user_id == AppUser.id).where(
        WorkspaceMember.workspace_id == ws, WorkspaceMember.papel == "admin", AppUser.ativo.is_(True))))


def default_alert(to: list[str], subject: str, body: str) -> None:
    s = get_settings()
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "alertas@prospector.local", ", ".join(to), subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as c:
            c.send_message(msg)
    except OSError:
        pass  # sem servidor de alerta: o aviso no painel continua valendo


def evaluate(session: Session, ws: int, now: datetime | None = None, alert: Callable[[str], None] | None = None) -> bool:
    """Retorna True se o disjuntor disparou agora."""
    s = get_settings()
    now = now or datetime.now(timezone.utc)
    if is_paused(session, ws):
        return False
    sent, bounced, rate = rates(session, ws, now)
    if sent < s.breaker_min_sent or rate <= s.breaker_bounce_rate:
        return False
    info = {"em": now.isoformat(), "taxa": round(rate, 4), "enviadas": sent, "bounces": bounced,
            "motivo": f"{bounced} de {sent} mensagens voltaram em 24 h ({rate:.1%}; o limite é {s.breaker_bounce_rate:.0%})."}
    wsrow = session.get(Workspace, ws)
    wsrow.config = {**(wsrow.config or {}), "envio_pausado": info}
    flag_modified(wsrow, "config")
    session.flush()
    texto = ("O Prospector PAUSOU todos os envios para proteger a reputação dos seus domínios.\n\n" + info["motivo"] +
             "\nConfira a lista de contatos e a qualidade dos emails antes de liberar o envio no painel (Disparo).")
    to = admin_emails(session, ws)
    if alert is not None:
        alert(texto)
    elif to:
        default_alert(to, "[Prospector] Envio pausado pelo disjuntor", texto)
    return True


def resume(session: Session, ws: int) -> None:
    wsrow = session.get(Workspace, ws)
    cfg = dict(wsrow.config or {})
    cfg.pop("envio_pausado", None)
    wsrow.config = cfg
    flag_modified(wsrow, "config")
    session.flush()
