"""Aceite da Etapa 2a contra o sistema em execução, com arquivos FICTÍCIOS no layout da base CNPJ.

ICP do aceite: CNAE 70.20-4, SP, ME/EPP. Confere: empresas com CNPJ + evidência em cada campo, sócios, conformidade,
progresso em tempo real, estimativa de custo do Places (sem gastar nada) e idempotência. Limpa tudo no final.

Uso: docker compose exec app python scripts/verificar_etapa2a.py
Usa ADMIN_EMAIL / ADMIN_PASSWORD do .env (a senha não é impressa).
"""
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, func, select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.core.models import (Company, ComplianceRecord, Contact, ContactRole, Evidence, IcpProfile, JobRun)  # noqa: E402
from tests.hunter.receita import cnpj_de, write_set  # noqa: E402

BASE = os.environ.get("BASE_URL", "http://localhost:8000")
c = httpx.Client(base_url=BASE, follow_redirects=False, timeout=60)
NOME_ICP = "ICP Consultoria SP (aceite)"
CNPJS = [cnpj_de("11111111"), cnpj_de("77777777")]


def tok(path):
    return re.search(r'name="csrf_token" value="([^"]+)"', c.get(path).text).group(1)


def check(ok, msg):
    print(("OK   " if ok else "FALHA"), msg)
    if not ok:
        limpar()
        sys.exit(1)


def esperar(cond, segundos=60):
    fim = time.time() + segundos
    while time.time() < fim:
        if cond():
            return True
        time.sleep(1)
    return False


def limpar():
    pasta = Path(get_settings().cnpj_dir)
    for f in pasta.glob("*"):
        if f.is_file() and ("ESTABELE" in f.name or "EMPRECSV" in f.name or "SOCIOCSV" in f.name or "MUNICCSV" in f.name):
            f.unlink()
    with SessionLocal() as s:
        ids = list(s.scalars(select(Company.id).where(Company.cnpj.in_(CNPJS))))
        cids = list(s.scalars(select(Contact.id).where(Contact.company_id.in_(ids)))) if ids else []
        if cids:
            s.execute(delete(ContactRole).where(ContactRole.contact_id.in_(cids)))
            s.execute(delete(Evidence).where(Evidence.entidade_tipo == "contact", Evidence.entidade_id.in_(cids)))
            s.execute(delete(ComplianceRecord).where(ComplianceRecord.entidade_tipo == "contact",
                                                     ComplianceRecord.entidade_id.in_(cids)))
            s.execute(delete(Contact).where(Contact.id.in_(cids)))
        if ids:
            s.execute(delete(Evidence).where(Evidence.entidade_tipo == "company", Evidence.entidade_id.in_(ids)))
            s.execute(delete(ComplianceRecord).where(ComplianceRecord.entidade_tipo == "company",
                                                     ComplianceRecord.entidade_id.in_(ids)))
            s.execute(delete(Company).where(Company.id.in_(ids)))
        s.execute(delete(JobRun).where(JobRun.tipo.in_(("coleta_cnpj", "coleta_places")),
                                       JobRun.parametros["icp"].astext.in_(
                                           [str(i) for i in s.scalars(select(IcpProfile.id).where(IcpProfile.nome == NOME_ICP))])))
        s.execute(delete(IcpProfile).where(IcpProfile.nome == NOME_ICP))
        s.commit()


def contar(model, *where):
    with SessionLocal() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


pasta = Path(get_settings().cnpj_dir)
if pasta.exists() and any(pasta.iterdir()):
    print("A pasta data/cnpj já tem arquivos; para não misturar com dados reais, este teste não roda.")
    sys.exit(2)
limpar()

r = c.post("/login", data={"email": os.environ["ADMIN_EMAIL"], "senha": os.environ["ADMIN_PASSWORD"], "csrf_token": tok("/login")})
check(r.status_code == 303, "login do administrador")
r = c.post("/configuracoes/icp", data={"nome": NOME_ICP, "cnaes": "70.20-4", "ufs": "SP", "portes": "ME, EPP",
                                       "termos_maps": "consultoria empresarial", "municipios": "São Paulo, Campinas",
                                       "csrf_token": tok("/configuracoes/icp/novo")})
check(r.status_code == 303, "perfil criado pelo painel (CNAE 70.20-4, SP, ME/EPP)")
with SessionLocal() as s:
    icp_id = s.scalar(select(IcpProfile.id).where(IcpProfile.nome == NOME_ICP))

pasta.mkdir(parents=True, exist_ok=True)
write_set(pasta)
r = c.post("/hunter/coleta/cnpj", data={"icp_id": icp_id, "csrf_token": tok("/hunter/coleta")})
check(r.status_code == 303, "importação da base CNPJ enfileirada")


def job_cnpj():
    with SessionLocal() as s:
        return s.scalar(select(JobRun).where(JobRun.tipo == "coleta_cnpj", JobRun.parametros["icp"].astext == str(icp_id)))


check(esperar(lambda: job_cnpj() is not None and job_cnpj().status != "em_andamento"), "worker terminou a importação")
job = job_cnpj()
check(job.status == "concluido" and job.contadores["criadas"] == 2, f"job concluído: {job.contadores['criadas']} empresas criadas")
with SessionLocal() as s:
    empresas = list(s.scalars(select(Company).where(Company.cnpj.in_(CNPJS))))
check(len(empresas) == 2 and all(e.cnpj and e.site_status == "nao_lido" for e in empresas), "2 empresas do perfil, com CNPJ")
ids = [e.id for e in empresas]
campos = {"cnpj", "razao_social", "uf", "municipio", "porte", "cnae_principal", "situacao_cadastral"}
for e in empresas:
    with SessionLocal() as s:
        ev = {x.campo for x in s.scalars(select(Evidence).where(Evidence.entidade_tipo == "company", Evidence.entidade_id == e.id))}
    check(campos <= ev, f"evidência (fonte, link, confiança) em cada campo — {e.razao_social}")
check(contar(ComplianceRecord, ComplianceRecord.entidade_tipo == "company", ComplianceRecord.entidade_id.in_(ids)) == 2,
      "registro de conformidade para cada empresa")
check(contar(Contact, Contact.company_id.in_(ids), Contact.origem == "cnpj_receita") == 3, "3 sócios/administradores como contatos")
html = c.get("/hunter/empresas").text
check("ALFA CONSULTORIA" in html.upper() and "Sao Paulo" in html, "empresas aparecem na lista do painel")
check("De onde vem cada dado" in c.get(f"/hunter/empresas/{ids[0]}").text, "página da empresa mostra a origem dos dados")
ev_txt = c.get(f"/hunter/coleta/{job.id}/eventos").text
dados = json.loads([l for l in ev_txt.splitlines() if l.startswith("data: ")][0][6:])
check(dados["status"] == "concluido" and dados["contadores"]["criadas"] == 2, "progresso em tempo real (SSE) funciona")

r = c.post("/hunter/coleta/places/estimar", data={"icp_id": icp_id, "csrf_token": tok("/hunter/coleta")})
check(r.status_code == 200 and "Custo estimado" in r.text and "2 consulta(s)" in r.text, "estimativa de custo do Google Places exibida antes de rodar")
r = c.post("/hunter/coleta/places/rodar", data={"icp_id": icp_id, "confirmo": "sim", "csrf_token": tok("/hunter/coleta")})
aviso = "GOOGLE_PLACES_API_KEY" in c.get("/hunter/coleta").text if not get_settings().google_places_api_key else True
check(r.status_code == 303 and aviso, "sem chave do Google, a busca paga não roda e o usuário é avisado")

antes = (contar(Company), contar(Contact), contar(Evidence))
c.post("/hunter/coleta/cnpj", data={"icp_id": icp_id, "csrf_token": tok("/hunter/coleta")})
time.sleep(5)
check(antes == (contar(Company), contar(Contact), contar(Evidence)), f"reimportar não duplica nada {antes}")
limpar()
check(contar(Company, Company.cnpj.in_(CNPJS)) == 0, "dados de teste removidos")
print("Critério de aceite da Etapa 2a: tudo certo.")
