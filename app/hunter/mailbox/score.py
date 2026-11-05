"""Nota de relacionamento (0-100): volume + mão dupla + recência + palavras-chave. Função pura e testável."""
import re
import unicodedata
from datetime import datetime, timezone

KEYWORDS = {
    "orcamento": re.compile(r"\borcamento"),
    "proposta": re.compile(r"\bproposta"),
    "contrato": re.compile(r"\bcontrato"),
    "nf": re.compile(r"\bnf-?e?\b|nota fiscal"),
    "reuniao": re.compile(r"\breuniao"),
}


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()


def detect_keywords(subject: str, body: str) -> list[str]:
    text = _norm(f"{subject}\n{body}")
    return [k for k, rx in KEYWORDS.items() if rx.search(text)]


def relationship_score(enviados: int, recebidos: int, ultima: datetime | None,
                       keywords: set[str] | list[str], now: datetime | None = None) -> int:
    total = enviados + recebidos
    if total <= 0:
        return 0
    volume = min(total, 20) / 20 * 30  # até 30
    if enviados > 0 and recebidos > 0:  # até 25: precisa ser conversa de mão dupla
        mao_dupla = 10 + 15 * (min(enviados, recebidos) / max(enviados, recebidos))
    else:
        mao_dupla = 0
    recencia = 3.0
    if ultima:
        now = now or datetime.now(timezone.utc)
        dias = (now - ultima).days
        recencia = 25 if dias <= 30 else 20 if dias <= 90 else 14 if dias <= 180 else 8 if dias <= 365 else 3
    palavras = min(len(set(keywords)), 4) * 5  # até 20
    return max(0, min(100, round(volume + mao_dupla + recencia + palavras)))
