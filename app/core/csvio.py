"""Importação/exportação CSV de empresas e contatos (Sprint 0).

Regras: tudo com evidência (fonte 'importacao_manual'), registro de conformidade por registro,
job idempotente pelo hash do arquivo e upsert (rodar duas vezes não duplica).
"""
import csv
import hashlib
import io
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.compliance import create_compliance_record
from app.core.jobs import finish_job, start_job
from app.core.models import Company, Contact, Department
from app.core.provenance import record_evidence
from app.web.forms import PORTES, UFS

ORIGEM = "importacao_manual"
CONFIANCA_IMPORTACAO = 0.7
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 20000

CONTATO_COLUNAS = ["nome_completo", "email", "cargo", "departamento", "cnpj", "dominio"]
EMPRESA_COLUNAS = ["cnpj", "razao_social", "nome_fantasia", "dominio", "site", "municipio", "uf", "porte"]
ALIASES = {
    "nome": "nome_completo", "name": "nome_completo", "e_mail": "email", "mail": "email",
    "setor": "departamento", "area": "departamento", "empresa": "razao_social",
    "razao": "razao_social", "fantasia": "nome_fantasia", "cidade": "municipio",
    "estado": "uf", "website": "site", "url": "site", "domain": "dominio",
}


@dataclass
class ImportResult:
    job_id: int | None = None
    arquivo_repetido: bool = False
    total: int = 0
    criados: int = 0
    atualizados: int = 0
    erros: list[tuple[int, str]] = field(default_factory=list)


class CsvError(ValueError):
    pass


def _norm_header(h: str) -> str:
    h = unicodedata.normalize("NFKD", h or "").encode("ascii", "ignore").decode().strip().lower()
    h = re.sub(r"[^a-z0-9]+", "_", h).strip("_")
    return ALIASES.get(h, h)


def read_csv(data: bytes) -> list[dict[str, str]]:
    if len(data) > MAX_BYTES:
        raise CsvError("Arquivo maior que 5 MB.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    first = text.splitlines()[0] if text.strip() else ""
    delimiter = ";" if first.count(";") > first.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = list(reader)
    if not rows:
        raise CsvError("Arquivo vazio.")
    headers = [_norm_header(h) for h in rows[0]]
    if len(rows) - 1 > MAX_ROWS:
        raise CsvError(f"Máximo de {MAX_ROWS} linhas por arquivo.")
    return [
        {headers[i]: (v or "").strip() for i, v in enumerate(r) if i < len(headers)}
        for r in rows[1:]
        if any(c.strip() for c in r)
    ]


def norm_domain(raw: str | None) -> str | None:
    d = (raw or "").strip().lower()
    d = re.sub(r"^https?://", "", d).split("/")[0].removeprefix("www.")
    return d or None


def only_digits(raw: str | None) -> str:
    return re.sub(r"\D", "", raw or "")


def cnpj_valido(cnpj: str) -> bool:
    if len(cnpj) != 14 or len(set(cnpj)) == 1:
        return False

    def dv(base: str) -> str:
        pesos = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        pesos = pesos[-len(base):]
        r = sum(int(d) * p for d, p in zip(base, pesos)) % 11
        return "0" if r < 2 else str(11 - r)

    d1 = dv(cnpj[:12])
    return cnpj[12] == d1 and cnpj[13] == dv(cnpj[:12] + d1)


def _evidences(session, ws, entity, entity_id, filename, **fields):
    for campo, valor in fields.items():
        if valor not in (None, ""):
            record_evidence(
                session, workspace_id=ws, entity_type=entity, entity_id=entity_id, field=campo,
                value=str(valor), source=ORIGEM, url=f"csv://{filename}", confidence=CONFIANCA_IMPORTACAO,
            )


def _begin(session: Session, ws: int, tipo: str, data: bytes, filename: str) -> tuple[ImportResult, bool]:
    sha = hashlib.sha256(data).hexdigest()
    job_id, created = start_job(session, tipo, {"sha256": sha}, ws)
    return ImportResult(job_id=job_id, arquivo_repetido=not created), created


def import_contacts(session: Session, ws: int, data: bytes, filename: str) -> ImportResult:
    rows = read_csv(data)
    res, created = _begin(session, ws, "import_contatos", data, filename)
    res.total = len(rows)
    if not created:
        return res
    deptos = {d.nome.lower(): d.id for d in session.scalars(select(Department).where(Department.workspace_id == ws))}
    for line, row in enumerate(rows, start=2):
        try:
            email = validate_email(row.get("email", ""), check_deliverability=False).normalized.lower()
        except EmailNotValidError as e:
            res.erros.append((line, f"Email inválido ({e})"))
            continue
        nome = row.get("nome_completo") or None
        depto_nome = row.get("departamento", "")
        depto_id = deptos.get(depto_nome.lower()) if depto_nome else None
        if depto_nome and depto_id is None:
            res.erros.append((line, f"Departamento desconhecido: {depto_nome}"))
            continue
        cnpj = only_digits(row.get("cnpj"))
        dominio = norm_domain(row.get("dominio"))
        company = None
        if cnpj:
            company = session.scalar(select(Company).where(Company.workspace_id == ws, Company.cnpj == cnpj))
        if company is None and dominio:
            company = session.scalar(select(Company).where(Company.workspace_id == ws, Company.dominio == dominio))
        contact = session.scalar(select(Contact).where(Contact.workspace_id == ws, Contact.email == email))
        values = {
            "nome_completo": nome,
            "primeiro_nome": nome.split()[0].capitalize() if nome else None,
            "cargo": row.get("cargo") or None,
            "department_id": depto_id,
            "company_id": company.id if company else None,
        }
        if contact is None:
            contact = Contact(workspace_id=ws, email=email, origem=ORIGEM, **values)
            session.add(contact)
            session.flush()
            res.criados += 1
        else:
            changed = False
            for k, v in values.items():
                if v is not None and getattr(contact, k) != v:
                    setattr(contact, k, v)
                    changed = True
            res.atualizados += 1 if changed else 0
        _evidences(session, ws, "contact", contact.id, filename, email=email, nome_completo=nome,
                   cargo=values["cargo"], departamento=depto_nome)
        create_compliance_record(session, workspace_id=ws, entity_type="contact", entity_id=contact.id, origem=ORIGEM)
    finish_job(session, res.job_id, {"total": res.total, "criados": res.criados,
                                     "atualizados": res.atualizados, "erros": len(res.erros)})
    return res


def import_companies(session: Session, ws: int, data: bytes, filename: str) -> ImportResult:
    rows = read_csv(data)
    res, created = _begin(session, ws, "import_empresas", data, filename)
    res.total = len(rows)
    if not created:
        return res
    for line, row in enumerate(rows, start=2):
        cnpj = only_digits(row.get("cnpj")) or None
        if cnpj and not cnpj_valido(cnpj):
            res.erros.append((line, f"CNPJ inválido: {row.get('cnpj')}"))
            continue
        uf = (row.get("uf") or "").upper() or None
        porte = (row.get("porte") or "").upper() or None
        if uf and uf not in UFS:
            res.erros.append((line, f"UF inválida: {uf}"))
            continue
        if porte and porte not in PORTES:
            res.erros.append((line, f"Porte inválido: {porte}"))
            continue
        dominio = norm_domain(row.get("dominio") or row.get("site"))
        razao, fantasia = row.get("razao_social") or None, row.get("nome_fantasia") or None
        if not (cnpj or dominio or razao or fantasia):
            res.erros.append((line, "Linha sem CNPJ, domínio ou nome da empresa"))
            continue
        company = None
        if cnpj:
            company = session.scalar(select(Company).where(Company.workspace_id == ws, Company.cnpj == cnpj))
        if company is None and dominio:
            company = session.scalar(select(Company).where(Company.workspace_id == ws, Company.dominio == dominio))
        if company is None and razao and not cnpj and not dominio:
            company = session.scalar(
                select(Company).where(Company.workspace_id == ws, func.lower(Company.razao_social) == razao.lower())
            )
        values = {"cnpj": cnpj, "razao_social": razao, "nome_fantasia": fantasia, "dominio": dominio,
                  "site": row.get("site") or None, "municipio": row.get("municipio") or None,
                  "uf": uf, "porte": porte}
        if company is None:
            company = Company(workspace_id=ws, **values)
            session.add(company)
            session.flush()
            res.criados += 1
        else:
            changed = False
            for k, v in values.items():
                if v is not None and getattr(company, k) != v:
                    setattr(company, k, v)
                    changed = True
            res.atualizados += 1 if changed else 0
        _evidences(session, ws, "company", company.id, filename, **values)
        create_compliance_record(session, workspace_id=ws, entity_type="company", entity_id=company.id, origem=ORIGEM)
    finish_job(session, res.job_id, {"total": res.total, "criados": res.criados,
                                     "atualizados": res.atualizados, "erros": len(res.erros)})
    return res


def safe_cell(value) -> str:
    """Evita 'injeção de fórmula' ao abrir o CSV no Excel."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _csv_stream(header: list[str], rows: Iterator[list]) -> Iterator[str]:
    buf = io.StringIO()
    writer = csv.writer(buf)
    yield "﻿"
    writer.writerow(header)
    for r in rows:
        writer.writerow([safe_cell(c) for c in r])
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate()
    yield buf.getvalue()


def export_companies(session: Session, ws: int) -> Iterator[str]:
    q = select(Company).where(Company.workspace_id == ws).order_by(Company.id)
    rows = ([getattr(c, col) for col in EMPRESA_COLUNAS] for c in session.scalars(q))
    return _csv_stream(EMPRESA_COLUNAS, rows)


def export_contacts(session: Session, ws: int) -> Iterator[str]:
    q = (
        select(Contact, Department.nome, Company.cnpj, Company.dominio)
        .outerjoin(Department, Contact.department_id == Department.id)
        .outerjoin(Company, Contact.company_id == Company.id)
        .where(Contact.workspace_id == ws)
        .order_by(Contact.id)
    )
    rows = ([c.nome_completo, c.email, c.cargo, d, cnpj, dom] for c, d, cnpj, dom in session.execute(q))
    return _csv_stream(CONTATO_COLUNAS, rows)


def template_csv(columns: list[str], example: list[str]) -> str:
    return "﻿" + ",".join(columns) + "\n" + ",".join(example) + "\n"
