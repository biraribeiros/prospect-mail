"""Etapa 3: orquestra duplicados, contador, tipo de email, departamento e papéis; e gera a lista bruta consolidada."""
import csv
import io
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import Company, Contact, Department
from app.hunter.companies.service import run_tracked
from app.hunter.resolution.classify import classify_contacts, detect_contadores
from app.hunter.resolution.dedupe import merge_all


def run_resolution(session: Session, ws: int) -> dict:
    params = {"dia": datetime.now(timezone.utc).isoformat(timespec="minutes")}

    def work(progress):
        mesclas = merge_all(session, ws)
        cont = detect_contadores(session, ws)
        cls = classify_contacts(session, ws)
        return {"mescladas": sum(mesclas.values()), "por_dominio": mesclas["dominio"], "por_telefone": mesclas["telefone"],
                "por_nome_cidade": mesclas["nome_cidade"], "contadores": cont["contadores"],
                "contatos_bloqueados": cont["contatos_bloqueados"], **cls}

    return run_tracked(session, ws, "resolucao", params, work)


COLUNAS = ["empresa", "cnpj", "dominio", "uf", "municipio", "nome", "cargo", "departamento", "email", "tipo_email",
           "status_email", "bloqueado", "motivo_bloqueio", "origem"]


def export_consolidated(session: Session, ws: int) -> str:
    """CSV 'lista bruta consolidada': um contato por linha, com a empresa já unificada."""
    depts = {d.id: d.nome for d in session.scalars(select(Department).where(Department.workspace_id == ws))}
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(COLUNAS)

    def safe(v) -> str:  # evita injeção de fórmulas ao abrir no Excel
        v = "" if v is None else str(v)
        return "'" + v if v[:1] in "=+-@\t\r" else v

    q = (select(Contact, Company).outerjoin(Company, Company.id == Contact.company_id)
         .where(Contact.workspace_id == ws).order_by(Company.id, Contact.id))
    for ct, c in session.execute(q):
        w.writerow([safe(c.nome_fantasia or c.razao_social) if c else "", safe(c.cnpj) if c else "",
                    safe(c.dominio) if c else "", safe(c.uf) if c else "", safe(c.municipio) if c else "",
                    safe(ct.nome_completo), safe(ct.cargo), safe(depts.get(ct.department_id, "")), safe(ct.email),
                    safe(ct.email_type), safe(ct.email_status), "sim" if ct.bloqueado_envio else "não",
                    safe(ct.bloqueio_motivo), safe(ct.origem)])
    return out.getvalue()
