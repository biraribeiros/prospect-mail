"""Etapa 10: limites por workspace (contatos, envios por dia, gasto mensal com APIs). 0 ou vazio = sem limite."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.models import Contact, JobRun, OutboundMessage, Workspace

CAMPOS = ("max_contatos", "envios_dia", "gasto_api_mes_usd")
TIPOS_QUE_CRIAM_CONTATOS = {"coleta_cnpj", "coleta_places", "coleta_sites", "scan_caixa"}
TIPOS_SEM_CUSTO = {"resolucao"}


class LimitExceeded(Exception):
    """Limite do workspace atingido. A mensagem diz qual e como resolver."""


def get_limits(session: Session, ws: int) -> dict[str, float]:
    raw = (session.get(Workspace, ws).limites or {})
    out: dict[str, float] = {}
    for k in CAMPOS:
        try:
            v = float(raw.get(k) or 0)
        except (TypeError, ValueError):
            v = 0.0
        out[k] = max(0.0, v)
    return out


def set_limits(session: Session, ws: int, values: dict[str, str | float | int | None]) -> dict[str, float]:
    row = session.get(Workspace, ws)
    novo = dict(row.limites or {})
    for k in CAMPOS:
        if k in values:
            try:
                v = float(values[k] or 0)
            except (TypeError, ValueError):
                raise ValueError(f"Valor inválido para {k}.")
            if v < 0:
                raise ValueError("Limites não podem ser negativos.")
            novo[k] = v
    row.limites = novo
    session.flush()
    return get_limits(session, ws)


def month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def usage(session: Session, ws: int, now: datetime | None = None) -> dict[str, float]:
    now = now or datetime.now(timezone.utc)
    tz = ZoneInfo("America/Sao_Paulo")
    local = now.astimezone(tz)
    ini = datetime.combine(local.date(), time(0, 0), tzinfo=tz).astimezone(timezone.utc)
    return {
        "contatos": session.scalar(select(func.count()).select_from(Contact).where(Contact.workspace_id == ws)) or 0,
        "envios_dia": session.scalar(select(func.count()).select_from(OutboundMessage).where(
            OutboundMessage.workspace_id == ws, OutboundMessage.status == "enviada", OutboundMessage.enviada_em >= ini,
            OutboundMessage.enviada_em < ini + timedelta(days=1))) or 0,
        "gasto_api_mes_usd": float(session.scalar(select(func.coalesce(func.sum(JobRun.custo_usd), 0)).where(
            JobRun.workspace_id == ws, JobRun.iniciado_em >= month_start(now))) or 0),
    }


def assert_job_allowed(session: Session, ws: int, tipo: str, now: datetime | None = None) -> None:
    lim, use = get_limits(session, ws), usage(session, ws, now)
    if lim["gasto_api_mes_usd"] and tipo not in TIPOS_SEM_CUSTO and use["gasto_api_mes_usd"] >= lim["gasto_api_mes_usd"]:
        raise LimitExceeded(f"Limite de gasto mensal com APIs atingido (US$ {use['gasto_api_mes_usd']:.2f} de US$ "
                            f"{lim['gasto_api_mes_usd']:.2f}). Um administrador pode aumentar em Operação.")
    if lim["max_contatos"] and tipo in TIPOS_QUE_CRIAM_CONTATOS and use["contatos"] >= lim["max_contatos"]:
        raise LimitExceeded(f"Limite de contatos do workspace atingido ({int(use['contatos'])} de {int(lim['max_contatos'])}).")


def send_room(session: Session, ws: int, now: datetime | None = None) -> int | None:
    """Quantos envios ainda cabem hoje no workspace (None = sem limite)."""
    lim = get_limits(session, ws)["envios_dia"]
    if not lim:
        return None
    return max(0, int(lim) - int(usage(session, ws, now)["envios_dia"]))
