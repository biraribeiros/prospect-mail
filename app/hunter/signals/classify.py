"""Classifica uma manchete/título em tipo de sinal comercial. Só regras simples e auditáveis (sem IA)."""
import re
import unicodedata

# (tipo, polaridade, validade em dias, padrões)
REGRAS: list[tuple[str, str, int, tuple[str, ...]]] = [
    ("inauguracao", "positivo", 60, ("inaugura", "nova unidade", "nova loja", "nova filial", "abre as portas")),
    ("expansao", "positivo", 60, ("expansao", "expande", "amplia", "novo escritorio", "nova sede", "investimento de", "aporte", "rodada de investimento")),
    ("lancamento", "positivo", 45, ("lanca ", "lancamento", "lancou", "nova linha", "novo produto", "novo servico")),
    ("diretoria", "positivo", 90, ("novo diretor", "nova diretora", "novo ceo", "nova ceo", "assume a direcao", "assume como diretor",
                                   "assume como diretora", "novo presidente", "nova presidente", "novo gerente de marketing",
                                   "muda diretoria", "mudanca na diretoria", "novo cmo")),
    ("feira", "positivo", 30, ("feira", "congresso", "participa do evento", "estande", "expo ")),
    ("crise", "negativo", 90, ("demissoes", "recuperacao judicial", "fecha unidade", "fecha as portas", "falencia")),
]


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", t) + " "


def classify_headline(title: str) -> tuple[str, str, int] | None:
    """Devolve (tipo, polaridade, validade_dias) ou None se a manchete não indica momento de compra."""
    t = _norm(title)
    for tipo, pol, dias, pats in REGRAS:
        if any(p in t for p in pats):
            return tipo, pol, dias
    return None


def mentions_company(title: str, names: list[str]) -> bool:
    """Só aceita a manchete se o nome da empresa aparece nela (evita homônimos e ruído)."""
    t = _norm(title)
    for n in names:
        n = _norm(n).strip()
        if len(n) >= 4 and n in t:
            return True
    return False
