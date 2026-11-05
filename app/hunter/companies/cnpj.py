"""Importador da base CNPJ da Receita Federal (dados abertos).

Lê os arquivos que você baixou para `data/cnpj/` (.zip ou já extraídos), filtra pelo perfil de cliente ideal (ICP) e
promove só o que interessa para `company` (+ sócios como `contact`). Não carrega a base inteira no banco.

Layout dos arquivos (CSV ISO-8859-1, separador ';', sem cabeçalho):
  Estabelecimentos: 0 cnpj_basico, 1 ordem, 2 dv, 3 matriz(1)/filial(2), 4 nome fantasia, 5 situação (02 = ativa),
    10 início atividade, 11 CNAE principal, 12 CNAEs secundários, 13 tipo logradouro, 14 logradouro, 15 número,
    17 bairro, 18 CEP, 19 UF, 20 município (código), 21 DDD1, 22 telefone1, 27 correio eletrônico
  Empresas: 0 cnpj_basico, 1 razão social, 5 porte (01 ME, 03 EPP, 05 demais)
  Sócios: 0 cnpj_basico, 1 tipo (1 PJ, 2 PF, 3 estrangeiro), 2 nome, 4 qualificação
  Municípios: 0 código, 1 nome
"""
import csv
import io
import re
import unicodedata
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.compliance import create_compliance_record
from app.core.csvio import cnpj_valido
from app.core.models import Company, Contact, ContactRole, IcpProfile
from app.core.provenance import record_evidence

FONTE = "cnpj_receita"
URL_FONTE = "https://dadosabertos.rfb.gov.br/CNPJ/"
CONF = 0.95
CONF_EMAIL_CADASTRAL = 0.3  # costuma ser do contador
PORTES = {"01": "ME", "03": "EPP", "05": "DEMAIS"}
ADMIN_QUALIF = {"05": "Administrador", "10": "Diretor", "16": "Presidente", "49": "Sócio-Administrador"}
KINDS = (("estab", ("ESTABELE",)), ("empresas", ("EMPRECSV", "EMPRESAS")), ("socios", ("SOCIOCSV", "SOCIOS")),
         ("municipios", ("MUNICCSV", "MUNICIPIOS")))
PARTICULAS = {"da", "de", "do", "das", "dos", "e"}
Progress = Callable[[dict], None]


class CnpjError(ValueError):
    pass


def unaccent_upper(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper().strip()


def nice_name(raw: str) -> str:
    words = raw.strip().lower().split()
    return " ".join(w if (w in PARTICULAS and i) else w.capitalize() for i, w in enumerate(words))


def fmt_cnae(code: str) -> str:
    return f"{code[:4]}-{code[4]}/{code[5:]}" if len(code) == 7 and code.isdigit() else code


def list_files(folder: str | Path) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {k: [] for k, _ in KINDS}
    base = Path(folder)
    if not base.is_dir():
        return out
    for f in sorted(base.iterdir()):
        if not f.is_file() or f.name.startswith("."):
            continue
        up = f.name.upper()
        for kind, pats in KINDS:
            if any(p in up for p in pats):
                out[kind].append(f)
                break
    return out


def files_signature(files: dict[str, list[Path]]) -> str:
    return ";".join(f"{f.name}:{f.stat().st_size}" for k in sorted(files) for f in files[k])


def iter_rows(path: Path) -> Iterator[list[str]]:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.endswith("/"):
                    continue
                with z.open(name) as raw:
                    yield from csv.reader(io.TextIOWrapper(raw, encoding="latin-1", newline=""), delimiter=";")
    else:
        with path.open(encoding="latin-1", newline="") as fh:
            yield from csv.reader(fh, delimiter=";")


@dataclass
class CnpjFilter:
    cnae_prefixes: list[str]
    ufs: set[str]
    municipio_nomes: set[str]  # normalizados (sem acento, maiúsculas)
    portes: set[str]  # ME/EPP/DEMAIS (MEI é ignorado: a Receita guarda isso em outro arquivo)
    avisos: list[str] = field(default_factory=list)


def build_filter(icp: IcpProfile) -> CnpjFilter:
    prefixes = [re.sub(r"\D", "", c) for c in icp.cnaes]
    flt = CnpjFilter(
        cnae_prefixes=[p for p in prefixes if p],
        ufs={u.upper() for u in icp.ufs},
        municipio_nomes={unaccent_upper(m) for m in icp.municipios},
        portes={p.upper() for p in icp.portes} & {"ME", "EPP", "DEMAIS"},
    )
    if not flt.cnae_prefixes and not flt.ufs:
        raise CnpjError("Defina pelo menos um CNAE ou um estado (UF) no perfil antes de importar a base CNPJ.")
    if "MEI" in {p.upper() for p in icp.portes}:
        flt.avisos.append("Porte MEI não é filtrado (a Receita guarda essa informação em outro arquivo).")
        if not flt.portes:
            pass  # só MEI: sem filtro de porte
    return flt


@dataclass
class Estab:
    ordem: str
    dv: str
    fantasia: str
    abertura: date | None
    cnae: str
    cnae_sec: list[str]
    endereco: str
    uf: str
    municipio_cod: str
    telefone: str | None
    email: str | None


def _date(v: str) -> date | None:
    try:
        return date(int(v[:4]), int(v[4:6]), int(v[6:8])) if len(v) == 8 and v != "00000000" else None
    except ValueError:
        return None


def load_municipios(files: list[Path]) -> dict[str, str]:
    return {r[0]: r[1] for p in files for r in iter_rows(p) if len(r) >= 2}


def pass_estab(files: list[Path], flt: CnpjFilter, municipio_codes: set[str] | None, cap: int,
               progress: Progress | None = None) -> tuple[dict[str, Estab], int, bool]:
    """Devolve ({cnpj_basico: Estab}, linhas_lidas, atingiu_limite). Só matrizes ativas dentro do ICP."""
    found: dict[str, Estab] = {}
    lidas, capped = 0, False
    for path in files:
        for r in iter_rows(path):
            lidas += 1
            if progress and lidas % 200_000 == 0:
                progress({"estabelecimentos_lidos": lidas, "encontrados": len(found)})
            if len(r) < 28 or r[3] != "1" or r[5] != "02":
                continue
            cnae = r[11]
            if flt.cnae_prefixes and not any(cnae.startswith(p) for p in flt.cnae_prefixes):
                continue
            if flt.ufs and r[19].upper() not in flt.ufs:
                continue
            if municipio_codes is not None and r[20] not in municipio_codes:
                continue
            phone = re.sub(r"\D", "", f"{r[21]}{r[22]}")
            end = ", ".join(x for x in (f"{r[13]} {r[14]}".strip(), r[15], r[17], r[18]) if x.strip())
            found[r[0]] = Estab(r[1], r[2], r[4].strip(), _date(r[10]), cnae,
                                [c for c in r[12].split(",") if c.strip()], end, r[19].upper(), r[20],
                                phone if len(phone) >= 10 else None, r[27].strip().lower() or None)
            if len(found) >= cap:
                return found, lidas, True
    return found, lidas, capped


def pass_empresas(files: list[Path], wanted: set[str], portes: set[str]) -> dict[str, tuple[str, str | None]]:
    out: dict[str, tuple[str, str | None]] = {}
    for path in files:
        for r in iter_rows(path):
            if len(r) < 6 or r[0] not in wanted:
                continue
            porte = PORTES.get(r[5])
            if portes and porte not in portes:
                continue
            out[r[0]] = (r[1].strip(), porte)
    return out


def pass_socios(files: list[Path], wanted: set[str]) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    for path in files:
        for r in iter_rows(path):
            if len(r) < 5 or r[0] not in wanted or r[1] != "2":  # só pessoas físicas
                continue
            out.setdefault(r[0], []).append((r[2].strip(), r[4].strip()))
    return out


def _evidence(session, ws, company_id, campo, valor, conf=CONF):
    if valor not in (None, ""):
        record_evidence(session, workspace_id=ws, entity_type="company", entity_id=company_id, field=campo,
                        value=str(valor), source=FONTE, url=URL_FONTE, confidence=conf)


def import_cnpj(session: Session, ws: int, icp: IcpProfile, folder: str | Path, cap: int,
                progress: Progress | None = None) -> dict:
    files = list_files(folder)
    if not files["estab"] or not files["empresas"]:
        raise CnpjError("Arquivos da base CNPJ não encontrados em data/cnpj/ (precisa de Estabelecimentos e Empresas).")
    flt = build_filter(icp)
    avisos = list(flt.avisos)
    municipios = load_municipios(files["municipios"]) if files["municipios"] else {}
    codes: set[str] | None = None
    if flt.municipio_nomes:
        if not municipios:
            raise CnpjError("O perfil tem cidades, mas falta o arquivo Municípios na pasta data/cnpj/.")
        codes = {c for c, n in municipios.items() if unaccent_upper(n) in flt.municipio_nomes}
        if not codes:
            raise CnpjError("Nenhuma das cidades do perfil foi encontrada no arquivo de municípios.")

    estabs, lidas, capped = pass_estab(files["estab"], flt, codes, cap, progress)
    if capped:
        avisos.append(f"Limite de {cap} empresas por importação atingido; refine o perfil para ver o restante.")
    if progress:
        progress({"estabelecimentos_lidos": lidas, "encontrados": len(estabs)})
    emp = pass_empresas(files["empresas"], set(estabs), flt.portes)
    socios = pass_socios(files["socios"], set(emp)) if files["socios"] else {}
    if not files["socios"]:
        avisos.append("Arquivo de sócios não encontrado: empresas importadas sem sócios.")

    created = updated = n_socios = invalidos = 0
    for i, (basico, (razao, porte)) in enumerate(emp.items(), start=1):
        e = estabs[basico]
        cnpj = f"{basico}{e.ordem}{e.dv}"
        if not cnpj_valido(cnpj):
            invalidos += 1
            continue
        values = {
            "razao_social": razao or None, "nome_fantasia": e.fantasia or None, "cnae_principal": fmt_cnae(e.cnae),
            "cnae_secundarios": [fmt_cnae(c) for c in e.cnae_sec], "uf": e.uf, "porte": porte,
            "municipio": nice_name(municipios.get(e.municipio_cod, e.municipio_cod)), "data_abertura": e.abertura,
            "situacao_cadastral": "ativa", "telefone": e.telefone, "endereco": e.endereco or None,
        }
        company = session.scalar(select(Company).where(Company.workspace_id == ws, Company.cnpj == cnpj))
        if company is None:
            company = Company(workspace_id=ws, cnpj=cnpj, **values)
            session.add(company)
            session.flush()
            created += 1
        else:
            changed = False
            for k, v in values.items():
                if v not in (None, [], "") and getattr(company, k) != v:
                    setattr(company, k, v)
                    changed = True
            updated += 1 if changed else 0
        _evidence(session, ws, company.id, "cnpj", cnpj)
        for campo, valor in values.items():
            _evidence(session, ws, company.id, campo, ",".join(valor) if isinstance(valor, list) else valor)
        _evidence(session, ws, company.id, "email_cadastral", e.email, CONF_EMAIL_CADASTRAL)
        create_compliance_record(session, workspace_id=ws, entity_type="company", entity_id=company.id, origem=FONTE)
        for nome, qualif in socios.get(basico, []):
            n_socios += _upsert_socio(session, ws, company.id, nome, qualif)
        if progress and i % 500 == 0:
            progress({"empresas_importadas": i, "criadas": created, "socios": n_socios})
    return {"estabelecimentos_lidos": lidas, "encontrados": len(estabs), "empresas_no_perfil": len(emp),
            "criadas": created, "atualizadas": updated, "socios": n_socios, "cnpj_invalidos": invalidos,
            "avisos": avisos}


def _upsert_socio(session: Session, ws: int, company_id: int, nome_raw: str, qualif: str) -> int:
    nome = nice_name(nome_raw)
    if not nome:
        return 0
    existing = session.scalar(select(Contact).where(
        Contact.workspace_id == ws, Contact.company_id == company_id, Contact.origem == FONTE,
        func.lower(Contact.nome_completo) == nome.lower()))
    cargo = ADMIN_QUALIF.get(qualif, "Sócio")
    novo = existing is None
    if novo:
        existing = Contact(workspace_id=ws, company_id=company_id, nome_completo=nome,
                           primeiro_nome=nome.split()[0], cargo=cargo, origem=FONTE)
        session.add(existing)
        session.flush()
    papel = "administrador" if qualif in ADMIN_QUALIF else "socio"
    if not session.scalar(select(ContactRole.id).where(ContactRole.contact_id == existing.id,
                                                       ContactRole.papel == papel)):
        session.add(ContactRole(workspace_id=ws, contact_id=existing.id, papel=papel, confianca=0.9, fonte=FONTE))
    for campo, valor in (("nome_completo", nome), ("cargo", cargo)):
        record_evidence(session, workspace_id=ws, entity_type="contact", entity_id=existing.id, field=campo,
                        value=valor, source=FONTE, url=URL_FONTE, confidence=CONF)
    create_compliance_record(session, workspace_id=ws, entity_type="contact", entity_id=existing.id, origem=FONTE)
    return 1 if novo else 0
