"""Etapa 9: estatística sem dependências — teste exato de Fisher (duas proporções) e veredito entre dois grupos."""
from dataclasses import dataclass
from math import comb

MIN_POR_GRUPO = 40
ALFA = 0.05


def fisher_two_sided(a: int, n1: int, c: int, n2: int) -> float:
    """p-valor exato (bicaudal) para: grupo 1 teve 'a' sucessos em n1; grupo 2 teve 'c' em n2."""
    k, n = a + c, n1 + n2
    lo, hi = max(0, k - n2), min(k, n1)
    denom = comb(n, k)

    def p(x: int) -> float:
        return comb(n1, x) * comb(n2, k - x) / denom
    p_obs = p(a)
    return min(1.0, sum(p(x) for x in range(lo, hi + 1) if p(x) <= p_obs * (1 + 1e-9)))


@dataclass
class Verdict:
    status: str  # insuficiente | aguardando | vencedor | empate
    vencedor: str | None
    p_valor: float | None
    texto: str


def compare_groups(nome_a: str, n_a: int, s_a: int, nome_b: str, n_b: int, s_b: int, *, minimo: int = MIN_POR_GRUPO,
                   maduros: bool = True, desfecho: str = "reunião em 21 dias") -> Verdict:
    if min(n_a, n_b) < minimo:
        return Verdict("insuficiente", None, None,
                       f"Dados insuficientes: cada grupo precisa de pelo menos {minimo} contatos abordados "
                       f"({nome_a}: {n_a}, {nome_b}: {n_b}).")
    if not maduros:
        return Verdict("aguardando", None, None, f"Aguardando a janela do critério ({desfecho}): ainda há contatos dentro do prazo.")
    p = fisher_two_sided(s_a, n_a, s_b, n_b)
    ra, rb = s_a / n_a, s_b / n_b
    if p < ALFA and ra != rb:
        w, r_w, r_l, l = (nome_a, ra, rb, nome_b) if ra > rb else (nome_b, rb, ra, nome_a)
        return Verdict("vencedor", w, p, f"{w} venceu: {r_w:.1%} contra {r_l:.1%} de {desfecho} (p={p:.3f}). "
                                          f"Priorize {w} e revise {l}.")
    return Verdict("empate", None, p, f"Sem diferença confiável entre {nome_a} ({ra:.1%}) e {nome_b} ({rb:.1%}); "
                                      f"p={p:.2f}. Continue coletando dados antes de decidir.")
