"""Cliente de IA (Claude) usado por enriquecimento, hipóteses, mensagens e classificação de respostas.

- Chamada direta à Messages API (httpx), com custo estimado a partir de usage e dos preços configuráveis.
- Cache de 30 dias por (modelo, instrução, texto) na tabela api_cache: a mesma pergunta nunca é paga duas vezes.
- Todo texto de terceiros entra como DADO dentro de <dados>…</dados>; a resposta é sempre validada por quem chama.
- Sem ANTHROPIC_API_KEY o cliente não existe (build_llm() devolve None) e cada módulo usa suas regras locais.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.models import ApiCache

URL = "https://api.anthropic.com/v1/messages"
CACHE_DAYS = 30


class LLMClient(Protocol):
    model: str

    def complete(self, system: str, user: str, max_tokens: int = 800) -> tuple[str, float]:
        """Devolve (texto, custo estimado em US$)."""


class ClaudeClient:
    def __init__(self, api_key: str, model: str, price_in: float, price_out: float, http: httpx.Client | None = None):
        self.api_key, self.model, self.price_in, self.price_out = api_key, model, price_in, price_out
        self._http = http or httpx.Client(timeout=60)

    def complete(self, system: str, user: str, max_tokens: int = 800) -> tuple[str, float]:
        r = self._http.post(URL, headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"},
                            json={"model": self.model, "max_tokens": max_tokens, "system": system,
                                  "output_config": {"effort": "low"},
                                  "messages": [{"role": "user", "content": user}]})
        r.raise_for_status()
        data = r.json()
        u = data.get("usage", {})
        cost = (u.get("input_tokens", 0) * self.price_in + u.get("output_tokens", 0) * self.price_out) / 1e6
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        return text, cost


def build_llm() -> LLMClient | None:
    s = get_settings()
    if not s.anthropic_api_key:
        return None
    return ClaudeClient(s.anthropic_api_key, s.anthropic_model, s.llm_price_in_per_mtok, s.llm_price_out_per_mtok)


def wrap_data(text: str, limit: int = 3000) -> str:
    """Texto de terceiros vira dado: sem a tag de fechamento, para não 'escapar' do bloco."""
    return "<dados>\n" + text[:limit].replace("</dados>", "") + "\n</dados>"


def extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        v = json.loads(m.group())
    except json.JSONDecodeError:
        return None
    return v if isinstance(v, dict) else None


def cached_complete(session: Session, ws: int, llm: LLMClient, system: str, user: str,
                    max_tokens: int = 800) -> tuple[str, float, bool]:
    """(texto, custo, veio_do_cache). Custo é 0 quando veio do cache."""
    key = hashlib.sha256(f"{llm.model}\n{system}\n{user}\n{max_tokens}".encode()).hexdigest()
    now = datetime.now(timezone.utc)
    row = session.scalar(select(ApiCache).where(ApiCache.workspace_id == ws, ApiCache.provedor == "llm",
                                                ApiCache.chave == key))
    if row and row.expira_em > now:
        return row.payload["text"], 0.0, True
    text, cost = llm.complete(system, user, max_tokens)
    exp = now + timedelta(days=CACHE_DAYS)
    if row:
        row.payload, row.expira_em, row.criado_em = {"text": text}, exp, now
    else:
        session.add(ApiCache(workspace_id=ws, provedor="llm", chave=key, payload={"text": text}, expira_em=exp))
    session.flush()
    return text, cost, False
