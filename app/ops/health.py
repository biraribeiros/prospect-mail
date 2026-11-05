"""Etapa 10: saúde da operação — jobs presos, backups e renovação de tokens OAuth."""
import smtplib
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.models import JobRun, MailboxAccount
from app.core.security import decrypt_json
from app.disparo.breaker import admin_emails


def _alert(session: Session, ws: int | None, subject: str, body: str) -> None:
    s = get_settings()
    to = admin_emails(session, ws) if ws else []
    if not to:
        return
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "alertas@prospector.local", ", ".join(to), f"[Prospector] {subject}"
    msg.set_content(body)
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as c:
            c.send_message(msg)
    except OSError:
        pass


# ---------- jobs presos ----------
def stuck_jobs(session: Session, now: datetime | None = None, hours: int | None = None) -> list[JobRun]:
    now = now or datetime.now(timezone.utc)
    limite = now - timedelta(hours=hours or get_settings().job_stuck_hours)
    return list(session.scalars(select(JobRun).where(JobRun.status == "em_andamento", JobRun.iniciado_em < limite)))


def reap_stuck_jobs(session: Session, now: datetime | None = None, hours: int | None = None, alert: Callable | None = None) -> int:
    """Marca como 'falhou' os jobs que passaram do prazo (worker caiu no meio) e avisa os administradores.
    Marcar como falho libera o mesmo job para rodar de novo (a trava de duplicidade ignora jobs que falharam)."""
    now = now or datetime.now(timezone.utc)
    jobs = stuck_jobs(session, now, hours)
    por_ws: dict[int | None, list[JobRun]] = {}
    for j in jobs:
        j.status, j.finalizado_em = "falhou", now
        j.contadores = {**(j.contadores or {}), "erro": "O processo parou de responder e foi encerrado automaticamente. Você pode rodar de novo."}
        por_ws.setdefault(j.workspace_id, []).append(j)
    session.flush()
    for ws, lst in por_ws.items():
        texto = "Estas tarefas passaram do prazo e foram encerradas:\n" + "\n".join(f"- {j.tipo} (iniciada em {j.iniciado_em:%d/%m %H:%M})" for j in lst)
        (alert or (lambda t, w=ws: _alert(session, w, "Tarefas presas encerradas", t)))(texto)
    return len(jobs)


# ---------- backups ----------
def list_backups(folder: str | Path) -> list[Path]:
    p = Path(folder)
    return sorted(p.glob("*.sql.gz"), key=lambda f: f.stat().st_mtime, reverse=True) if p.exists() else []


def backup_status(folder: str | Path | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    files = list_backups(folder or get_settings().backup_dir)
    if not files:
        return {"ok": False, "ultimo": None, "idade_h": None, "quantidade": 0, "aviso": "Nenhum backup encontrado. Confira o serviço 'backup' no docker-compose."}
    last = datetime.fromtimestamp(files[0].stat().st_mtime, tz=timezone.utc)
    idade = (now - last).total_seconds() / 3600
    return {"ok": idade <= 26 and files[0].stat().st_size > 0, "ultimo": last, "idade_h": round(idade, 1), "quantidade": len(files),
            "tamanho_mb": round(files[0].stat().st_size / 1e6, 2),
            "aviso": None if idade <= 26 else f"O último backup tem {idade:.0f} horas. Deveria ser diário."}


def prune_backups(folder: str | Path, keep_days: int = 30, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    n = 0
    for f in list_backups(folder):
        if datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc) < now - timedelta(days=keep_days):
            f.unlink()
            n += 1
    return n


# ---------- tokens OAuth ----------
def refresh_oauth_tokens(session: Session, refreshers: dict[str, Callable[[dict], object]] | None = None) -> dict:
    """Renova de verdade (todo dia) o token de cada caixa Gmail/Microsoft e marca a saúde da caixa: se o provedor revogou o acesso,
    o painel avisa antes de a varredura quebrar."""
    from app.hunter.mailbox.oauth import refresh_google_token, refresh_microsoft_token

    refreshers = refreshers or {
        "gmail": lambda c: refresh_google_token(c["client_id"], c["client_secret"], c["refresh_token"]),
        "graph": lambda c: refresh_microsoft_token(c.get("tenant", "common"), c["client_id"], c["client_secret"], c["refresh_token"]),
    }
    out = {"ok": 0, "erro": 0}
    for mb in session.scalars(select(MailboxAccount).where(MailboxAccount.tipo.in_(tuple(refreshers)))):
        try:
            refreshers[mb.tipo](decrypt_json(mb.tokens_enc))
            mb.saude = "ok"
            out["ok"] += 1
        except Exception:  # noqa: BLE001 - qualquer falha = pedir nova autorização
            mb.saude = "reautorizar"
            out["erro"] += 1
            _alert(session, mb.workspace_id, "Caixa precisa de nova autorização", f"A caixa '{mb.nome}' não conseguiu renovar o acesso. Conecte-a de novo.")
    session.flush()
    return out
