"""Leitura e validação dos campos dos formulários (listas, UF, porte, CNAE, necessidades)."""
import re

UFS = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA",
    "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
}
PORTES = {"MEI", "ME", "EPP", "DEMAIS"}


def split_list(text: str | None) -> list[str]:
    """'a, b\\nc' -> ['a','b','c'] sem vazios nem repetidos (mantém a ordem)."""
    seen, out = set(), []
    for item in re.split(r"[\n,;]+", text or ""):
        item = item.strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


def norm_cnae(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 5:
        return f"{digits[:2]}.{digits[2:4]}-{digits[4]}"
    if len(digits) == 7:
        return f"{digits[:4]}-{digits[4]}/{digits[5:]}"
    if len(digits) in (2, 3, 4):
        return digits
    return None


def parse_icp(nome: str, cnaes: str, ufs: str, municipios: str, portes: str, termos_maps: str, deptos: list[str]):
    errors: list[str] = []
    nome = (nome or "").strip()
    if not nome:
        errors.append("Dê um nome ao perfil.")
    cnae_list = []
    for c in split_list(cnaes):
        n = norm_cnae(c)
        if n is None:
            errors.append(f"CNAE inválido: {c} (ex.: 70.20-4)")
        else:
            cnae_list.append(n)
    uf_list = [u.upper() for u in split_list(ufs)]
    bad = [u for u in uf_list if u not in UFS]
    if bad:
        errors.append(f"UF inválida: {', '.join(bad)}")
    porte_list = [p.upper() for p in split_list(portes)]
    bad = [p for p in porte_list if p not in PORTES]
    if bad:
        errors.append(f"Porte inválido: {', '.join(bad)} (use MEI, ME, EPP ou DEMAIS)")
    data = {
        "nome": nome,
        "cnaes": cnae_list,
        "ufs": uf_list,
        "municipios": split_list(municipios),
        "portes": porte_list,
        "termos_maps": split_list(termos_maps),
        "departamentos_alvo": deptos,
    }
    return data, errors


def norm_pattern(p: str) -> str | None:
    p = p.strip().lower().rstrip("@")
    if not re.fullmatch(r"[a-z0-9._-]+", p):
        return None
    return p + "@"


def parse_necessidades(text: str | None) -> tuple[list[dict], list[str]]:
    """Uma por linha: 'Necessidade | relevância 1-5 | tag1, tag2'."""
    out, errors = [], []
    for n, line in enumerate((text or "").splitlines(), start=1):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split("|")]
        nome = parts[0]
        try:
            rel = int(parts[1]) if len(parts) > 1 and parts[1] else 3
        except ValueError:
            rel = 0
        if not nome or not 1 <= rel <= 5:
            errors.append(f"Linha {n}: use 'Necessidade | relevância de 1 a 5 | tags'")
            continue
        tags = split_list(parts[2]) if len(parts) > 2 else []
        out.append({"necessidade": nome, "relevancia": rel, "ofertas_relacionadas": tags})
    return out, errors


def necessidades_to_text(items: list[dict]) -> str:
    return "\n".join(
        f"{i['necessidade']} | {i['relevancia']} | {', '.join(i.get('ofertas_relacionadas', []))}" for i in items
    )


def parse_department(nome: str, patterns: str, cargos: str, necessidades: str):
    errors: list[str] = []
    nome = (nome or "").strip()
    if not nome:
        errors.append("Dê um nome ao departamento.")
    pats = []
    for p in split_list(patterns):
        n = norm_pattern(p)
        if n is None:
            errors.append(f"Padrão de email inválido: {p} (ex.: rh@)")
        else:
            pats.append(n)
    needs, nerrors = parse_necessidades(necessidades)
    errors.extend(nerrors)
    return {"nome": nome, "email_patterns": pats, "cargos": split_list(cargos), "necessidades_previstas": needs}, errors
