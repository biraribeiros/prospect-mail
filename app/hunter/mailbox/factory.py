"""Monta o leitor certo a partir de uma caixa cadastrada (segredos decifrados só aqui, em memória)."""
import ipaddress
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.models import MailboxAccount
from app.core.security import decrypt_json
from app.hunter.mailbox.base import MailboxReader
from app.hunter.mailbox.gmail import GmailAdapter
from app.hunter.mailbox.graph import GraphAdapter
from app.hunter.mailbox.imap import ImapAdapter
from app.hunter.mailbox.llm import ClaudeSignatureExtractor
from app.hunter.mailbox.mbox import MboxAdapter
from app.hunter.mailbox.oauth import OAuthToken, refresh_google_token, refresh_microsoft_token


def mbox_dir(workspace_id: int) -> Path:
    return Path(get_settings().data_dir).resolve() / "mbox" / str(workspace_id)


def safe_mbox_path(workspace_id: int, path: str) -> Path:
    """Só arquivos dentro de data/mbox/<workspace>: evita ler arquivo arbitrário do servidor."""
    p, base = Path(path).resolve(), mbox_dir(workspace_id)
    if base not in p.parents and p != base:
        raise ValueError("Caminho de arquivo fora da pasta permitida.")
    return p


def validate_imap_host(host: str) -> str | None:
    """Mensagem de erro, ou None se ok. Bloqueia IPs locais/privados e nomes internos (evita uso do servidor
    como ponte para a rede interna)."""
    host = host.strip().lower()
    if not host or " " in host or host in {"localhost"} or host.endswith((".local", ".internal", ".localhost")):
        return "Servidor IMAP inválido."
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None if "." in host else "Informe o endereço completo do servidor (ex.: imap.seudominio.com.br)."
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        return "Endereços de rede interna não são permitidos."
    return None


def build_reader(mailbox: MailboxAccount) -> MailboxReader:
    cfg = decrypt_json(mailbox.tokens_enc)
    if mailbox.tipo == "mbox":
        return MboxAdapter(safe_mbox_path(mailbox.workspace_id, cfg["path"]), cfg.get("pasta_hint"))
    if mailbox.tipo == "imap":
        return ImapAdapter(cfg["host"], int(cfg["port"]), cfg["user"], cfg["password"], cfg.get("security", "ssl"))
    if mailbox.tipo == "gmail":
        token = OAuthToken(lambda: refresh_google_token(cfg["client_id"], cfg["client_secret"], cfg["refresh_token"]))
        return GmailAdapter(token, history_id=(mailbox.cursor or {}).get("historyId"))
    if mailbox.tipo == "graph":
        token = OAuthToken(lambda: refresh_microsoft_token(
            cfg.get("tenant", "common"), cfg["client_id"], cfg["client_secret"], cfg["refresh_token"]))
        return GraphAdapter(token)
    raise ValueError(f"Tipo de caixa desconhecido: {mailbox.tipo}")


def own_addresses(session: Session, workspace_id: int) -> set[str]:
    """Emails das caixas do workspace: nunca viram contatos."""
    out: set[str] = set()
    for mb in session.scalars(select(MailboxAccount).where(MailboxAccount.workspace_id == workspace_id)):
        if mb.tokens_enc:
            try:
                out |= {e.lower() for e in decrypt_json(mb.tokens_enc).get("emails", [])}
            except Exception:  # noqa: BLE001
                continue
    return out


def build_llm() -> ClaudeSignatureExtractor | None:
    s = get_settings()
    if not s.anthropic_api_key:
        return None
    return ClaudeSignatureExtractor(s.anthropic_api_key, s.anthropic_model, s.llm_price_in_per_mtok,
                                    s.llm_price_out_per_mtok)
