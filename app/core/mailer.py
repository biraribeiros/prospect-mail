"""Email interno do sistema (alertas, resumo semanal, cartão no Trello). Nunca é email de prospecção.

Em desenvolvimento aponta para o Mailpit (SMTP_HOST/SMTP_PORT, sem login). Em produção configure um SMTP real no .env:
SMTP_HOST, SMTP_PORT, SMTP_SECURITY (none | starttls | ssl), SMTP_USER, SMTP_PASSWORD e SMTP_FROM.
"""
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import get_settings


def send_mail(to: list[str], subject: str, body: str, from_addr: str | None = None) -> None:
    """Envia um email de texto. Levanta OSError/SMTPException se falhar (quem chama decide o que fazer)."""
    s = get_settings()
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = from_addr or s.smtp_from, ", ".join(to), subject
    msg.set_content(body)
    sec = (s.smtp_security or "none").lower()
    ctx = ssl.create_default_context()
    c = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=ctx, timeout=15) if sec == "ssl" else smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15)
    with c:
        if sec == "starttls":
            c.starttls(context=ctx)
        if s.smtp_user:
            c.login(s.smtp_user, s.smtp_password)
        c.send_message(msg)
