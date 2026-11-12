"""Entrada simples de emails: (1) lista em qualquer texto ou arquivo e (2) lotes enviados pela extensão do navegador.

Tudo vira contato 'nao_verificado' com evidência da fonte e registro de conformidade; o Filtro valida depois.
Nada é enviado a ninguém aqui. Ruído (robôs, bancos, redes sociais) é descartado pelo mesmo filtro das caixas.
"""
import hashlib
import html
import io
import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.compliance import create_compliance_record
from app.core.jobs import finish_job, start_job
from app.core.limits import get_limits
from app.core.models import Company, Contact, Workspace
from app.core.provenance import record_evidence
from app.hunter.mailbox.factory import own_addresses
from app.hunter.mailbox.noise import NoiseFilter, domain_of, dominio_tipo

ORIGEM_LISTA = "lista_texto"
ORIGEM_EXTENSAO = "extensao_navegador"
CONFIANCA_LISTA = 0.6  # lista que o próprio usuário forneceu
CONFIANCA_EXTENSAO = 0.4  # email visto numa página qualquer da web
MAX_ARQUIVO = 10 * 1024 * 1024
MAX_ZIP_DESCOMPRIMIDO = 50 * 1024 * 1024
MAX_EMAILS_LISTA = 20000

_AT = re.compile(r"\s*[\[(\{]\s*(?:at|arroba)\s*[\])\}]\s*", re.I)  # "nome [at] empresa (dot) com"
_DOT = re.compile(r"\s*[\[(\{]\s*(?:dot|ponto)\s*[\])\}]\s*", re.I)
EMAIL_RE = re.compile(r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,24}")
TLDS_FALSOS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "css", "js", "ico", "woff", "woff2", "ttf", "map", "pdf"}


class IngestError(ValueError):
    pass


@dataclass
class IngestResult:
    job_id: int | None = None
    arquivo_repetido: bool = False
    encontrados: int = 0  # emails distintos com cara de email (antes de tirar ruído)
    novos: int = 0
    ja_existiam: int = 0
    ruido: int = 0
    invalidos: int = 0
    limite: bool = False
    amostra: list[str] = field(default_factory=list)


def texts_of(data: bytes) -> list[str]:
    """Textos possíveis de um arquivo qualquer: texto puro/CSV/HTML/EML/RTF, DOCX/XLSX/ODT/PPTX (zip de XML) e DOC antigo."""
    if len(data) > MAX_ARQUIVO:
        raise IngestError("Arquivo maior que 10 MB.")
    if data[:5] == b"%PDF-":
        raise IngestError("PDF não é lido (o texto fica comprimido). Copie o texto e cole na caixa, ou salve como .txt.")
    if data[:4] == b"PK\x03\x04":
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise IngestError("Arquivo corrompido ou em formato desconhecido.") from e
        total, parts = 0, []
        for info in z.infolist():
            if not info.filename.lower().endswith((".xml", ".rels", ".txt", ".html", ".htm", ".csv")):
                continue
            total += info.file_size
            if total > MAX_ZIP_DESCOMPRIMIDO:
                raise IngestError("Arquivo compactado grande demais.")
            parts.append(z.read(info).decode("utf-8", "ignore"))
        return [html.unescape("\n".join(parts))]
    # utf-16 (DOC antigo, exportações do Windows) com e sem deslocamento de 1 byte
    brutos = [data.decode("utf-8", "ignore"), data.decode("utf-16-le", "ignore"), data[1:].decode("utf-16-le", "ignore")]
    return [html.unescape(t) for t in brutos]


def extract_emails(*texts: str) -> list[str]:
    seen: dict[str, None] = {}
    for t in texts:
        for m in EMAIL_RE.findall(_DOT.sub(".", _AT.sub("@", t))):
            seen.setdefault(m.lower().strip("."))
    return list(seen)


def _normalize(raw: str) -> str | None:
    e = (raw or "").strip().lower()
    if e.rsplit(".", 1)[-1] in TLDS_FALSOS:
        return None
    try:
        return validate_email(e, check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return None


def ingest_emails(session: Session, ws: int, candidatos: Iterable[str], *, origem: str, fonte: str,
                  confianca: float) -> IngestResult:
    res = IngestResult()
    noise = NoiseFilter.from_config((session.get(Workspace, ws).config or {}))
    proprios = own_addresses(session, ws)  # emails das suas próprias caixas nunca viram contato
    vistos: set[str] = set()
    limpos: list[str] = []
    for raw in candidatos:
        e = _normalize(raw)
        if e is None:
            res.invalidos += 1
            continue
        if e in vistos:
            continue
        vistos.add(e)
        if e in proprios or noise.is_noise_address(e):
            res.ruido += 1
            continue
        limpos.append(e)
    res.encontrados = len(vistos)
    if not limpos:
        return res
    existentes = set(session.scalars(select(Contact.email).where(Contact.workspace_id == ws, Contact.email.in_(limpos))))
    res.ja_existiam = len(existentes)
    novos = [e for e in limpos if e not in existentes]
    teto = get_limits(session, ws)["max_contatos"]
    sobra = None
    if teto:
        usados = session.scalar(select(func.count()).select_from(Contact).where(Contact.workspace_id == ws)) or 0
        sobra = max(0, int(teto) - usados)
    doms = {domain_of(e) for e in novos if dominio_tipo(e) == "corporativo"}
    empresas: dict[str, int] = {}
    if doms:
        for dom, cid in session.execute(select(Company.dominio, Company.id).where(
                Company.workspace_id == ws, Company.dominio.in_(doms)).order_by(Company.id.desc())):
            empresas[dom] = cid
    for e in novos:
        if sobra is not None and res.novos >= sobra:
            res.limite = True
            break
        try:
            with session.begin_nested():  # dois lotes simultâneos com o mesmo email: o segundo só conta como existente
                c = Contact(workspace_id=ws, email=e, origem=origem, dominio_tipo=dominio_tipo(e),
                            company_id=empresas.get(domain_of(e)))
                session.add(c)
                session.flush()
        except IntegrityError:
            res.ja_existiam += 1
            continue
        record_evidence(session, workspace_id=ws, entity_type="contact", entity_id=c.id, field="email", value=e,
                        source=origem, url=fonte[:500], confidence=confianca)
        create_compliance_record(session, workspace_id=ws, entity_type="contact", entity_id=c.id, origem=origem)
        res.novos += 1
        if len(res.amostra) < 5:
            res.amostra.append(e)
    return res


def import_text_list(session: Session, ws: int, data: bytes, nome_arquivo: str) -> IngestResult:
    emails = extract_emails(*texts_of(data))
    if len(emails) > MAX_EMAILS_LISTA:
        raise IngestError(f"Máximo de {MAX_EMAILS_LISTA} emails por vez. Divida a lista.")
    job_id, criado = start_job(session, "import_lista_texto", {"sha256": hashlib.sha256(data).hexdigest()}, ws)
    if not criado:  # a mesma lista duas vezes não faz nada
        return IngestResult(job_id=job_id, arquivo_repetido=True, encontrados=len(emails))
    res = ingest_emails(session, ws, emails, origem=ORIGEM_LISTA, fonte=f"lista://{nome_arquivo[:200]}",
                        confianca=CONFIANCA_LISTA)
    res.job_id = job_id
    finish_job(session, job_id, {"encontrados": res.encontrados, "novos": res.novos, "ja_existiam": res.ja_existiam,
                                 "ruido": res.ruido, "invalidos": res.invalidos})
    return res
