"""Leitura de assinatura de email por regras (nome, cargo, telefone, site). A IA é só reserva (llm.py)."""
import re
from dataclasses import dataclass

CARGO_RE = re.compile(
    r"\b(diretor|diretora|gerente|coordenador|coordenadora|analista|s[oó]cio|s[oó]cia|ceo|cto|cfo|coo|cmo|"
    r"propriet[aá]rio|propriet[aá]ria|supervisor|supervisora|assistente|consultor|consultora|executivo|"
    r"executiva|gestor|gestora|presidente|head|manager|director|founder|fundador|fundadora|especialista|"
    r"t[eé]cnico|t[eé]cnica|engenheiro|engenheira|advogado|advogada|administrador|administradora|"
    r"representante|vendedor|vendedora|sócio-administrador)\b", re.I)
CLOSING_RE = re.compile(
    r"^(atenciosamente|att\.?|abra[cç]os?|abra[cç]o|cordialmente|obrigad[oa]|grato|grata|"
    r"best regards|kind regards|regards|sincerely|thanks|at\.te|um abra[cç]o|forte abra[cç]o)[,!.]?\s*$", re.I)
QUOTE_START_RE = re.compile(
    r"^(em .{5,80} escreveu:|on .{5,80} wrote:|-{2,}\s*(mensagem original|original message|forwarded).*|"
    r"_{5,}|de:\s.+|from:\s.+)$", re.I)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?55[\s.-]?)?\(?\d{2}\)?[\s.-]?9?\d{4}[\s.-]?\d{4}(?!\d)")
SITE_RE = re.compile(r"(?:https?://[^\s<>()]+|www\.[^\s<>()]+)", re.I)
SPLIT_RE = re.compile(r"\s*[|•·]\s*|\s+[-–—]\s+")
NAME_TOKEN = re.compile(r"^[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\w'’.-]*$|^(?:da|de|do|das|dos|e)$")


@dataclass
class SignatureInfo:
    nome: str | None = None
    cargo: str | None = None
    telefone: str | None = None
    site: str | None = None
    trecho: str = ""

    @property
    def tem_dados(self) -> bool:
        return any((self.nome, self.cargo, self.telefone, self.site))


def strip_quoted(body: str) -> str:
    out = []
    for line in body.replace("\r", "").split("\n"):
        s = line.strip()
        if QUOTE_START_RE.match(s):
            break
        if s.startswith(">"):
            continue
        out.append(line.rstrip())
    return "\n".join(out)


def signature_lines(body: str) -> list[str]:
    lines = [l.strip() for l in strip_quoted(body).split("\n")]
    for i, l in enumerate(lines):  # delimitador clássico "-- "
        if l == "--":
            return [x for x in lines[i + 1:i + 14] if x]
    tail = lines[-30:]
    for i in range(len(tail) - 1, -1, -1):  # última despedida ("Atenciosamente,")
        if CLOSING_RE.match(tail[i]):
            return [x for x in tail[i + 1:i + 14] if x]
    return [x for x in lines if x][-8:]


def _is_name(text: str) -> bool:
    if re.search(r"[\d@:/]", text) or len(text) > 45 or CARGO_RE.search(text):
        return False
    tokens = text.split()
    return 2 <= len(tokens) <= 5 and all(NAME_TOKEN.match(t) for t in tokens)


def parse_signature(body: str) -> SignatureInfo:
    lines = signature_lines(body)
    info = SignatureInfo(trecho="\n".join(lines)[:400])
    for line in lines:
        for part in SPLIT_RE.split(line):
            part = part.strip(" ,;")
            if not part:
                continue
            if info.cargo is None and CARGO_RE.search(part) and len(part) <= 80 and "@" not in part \
                    and not SITE_RE.search(part):
                info.cargo = part
            elif info.nome is None and _is_name(part):
                info.nome = part
    text = "\n".join(lines)
    for m in PHONE_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 10 <= len(digits) <= 13:
            info.telefone = digits
            break
    m = SITE_RE.search(text)
    if m:
        info.site = m.group().rstrip(".,;)")
    return info
