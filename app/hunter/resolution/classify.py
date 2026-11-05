"""Etapa 3: tipo do email (pessoal / de função / genérico), departamento, papel de compra e detecção de contador."""
import re
import unicodedata

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.models import Company, Contact, ContactRole, Department, Evidence
from app.hunter.mailbox.noise import domain_of
from app.hunter.resolution.dedupe import DOMINIOS_GENERICOS

GENERICOS = {"contato", "contact", "info", "geral", "atendimento", "sac", "faleconosco", "ola", "hello", "oi", "mail",
             "email", "secretaria", "recepcao", "webmaster", "admin", "administrativo"}
FUNCAO = {"comercial", "vendas", "financeiro", "rh", "recrutamento", "marketing", "comunicacao", "imprensa", "compras",
          "diretoria", "juridico", "ti", "suporte", "sst", "seguranca", "eventos", "operacoes", "qualidade", "gente",
          "talentos", "midia", "negocios", "cipa", "fiscal", "contabil", "logistica"}
CONTADOR_DOMINIOS_MIN = 20  # mesmo domínio de email em mais de 20 CNPJs = escritório de contabilidade
CNAE_CONTADOR_PREFIXO = "69"  # grupo 69: atividades jurídicas, de contabilidade e auditoria


def _ascii(v: str) -> str:
    return unicodedata.normalize("NFKD", v or "").encode("ascii", "ignore").decode().lower()


def email_type(email: str) -> str:
    local = _ascii(email.split("@")[0])
    partes = re.split(r"[._+-]", local)
    base, resto = partes[0], partes[1:]
    curto = all(len(p) <= 3 for p in resto)  # sufixos como "sp", "rj", "01": marketing.sp@ continua sendo de função
    if local in GENERICOS or (base in GENERICOS and curto):
        return "generico"
    if local in FUNCAO or (base in FUNCAO and curto):
        return "funcao"
    return "pessoal"


# cargo → papel de compra (confiança 0,6: é inferência pelo título)
PAPEIS: list[tuple[str, tuple[str, ...]]] = [
    ("decisor", ("diretor", "diretora", "ceo", "presidente", "socio", "dono", "proprietario", "head", "gerente geral", "owner")),
    ("orcamento", ("financeiro", "compras", "controller", "cfo", "tesoureiro")),
    ("aprovador", ("gerente", "coordenador", "coordenadora", "supervisor")),
    ("influenciador", ("analista", "especialista", "consultor", "assessor", "produtor")),
    ("usuario", ("assistente", "auxiliar", "estagiario", "atendente", "operador")),
    ("gatekeeper", ("secretaria", "recepcao", "recepcionista")),
]


def infer_role(cargo: str | None) -> str | None:
    c = _ascii(cargo or "")
    if not c:
        return None
    for papel, kws in PAPEIS:
        if any(re.search(rf"\b{k}", c) for k in kws):
            return papel
    return None


def link_department(session: Session, ws: int, contact: Contact, depts: list[Department]) -> int | None:
    """Departamento pelo cargo (maior sobreposição de palavras) ou pelo padrão de email (ex.: marketing@)."""
    local = _ascii((contact.email or "").split("@")[0])
    cargo = _ascii(contact.cargo or "")
    for d in depts:
        if any(local == _ascii(p.rstrip("@")) for p in d.email_patterns):
            return d.id
    if cargo:
        best, score = None, 0
        palavras = set(re.findall(r"[a-z]{4,}", cargo))
        for d in depts:
            alvo = set(re.findall(r"[a-z]{4,}", _ascii(" ".join(d.cargos) + " " + d.nome)))
            n = len(palavras & alvo)
            if n > score:
                best, score = d.id, n
        if best:
            return best
    return None


def classify_contacts(session: Session, ws: int) -> dict:
    depts = list(session.scalars(select(Department).where(Department.workspace_id == ws)))
    out = {"tipados": 0, "com_departamento": 0, "papeis": 0}
    for ct in session.scalars(select(Contact).where(Contact.workspace_id == ws)):
        if ct.email:
            t = email_type(ct.email)
            if ct.email_type != t:
                ct.email_type = t
                out["tipados"] += 1
        if ct.department_id is None:
            dep = link_department(session, ws, ct, depts)
            if dep:
                ct.department_id = dep
                out["com_departamento"] += 1
        papel = infer_role(ct.cargo)
        if papel and not session.scalar(select(ContactRole.id).where(ContactRole.contact_id == ct.id,
                                                                     ContactRole.papel == papel)):
            session.add(ContactRole(workspace_id=ws, contact_id=ct.id, papel=papel, confianca=0.6, fonte="cargo"))
            out["papeis"] += 1
    session.flush()
    return out


def detect_contadores(session: Session, ws: int) -> dict:
    """Marca empresas de contabilidade (CNAE grupo 69) e bloqueia contatos de domínios que aparecem em muitos CNPJs
    (o email cadastral na Receita costuma ser do escritório contábil, não da empresa)."""
    marcadas = 0
    digits_cnae = func.regexp_replace(Company.cnae_principal, r"\D", "", "g")
    for c in session.scalars(select(Company).where(Company.workspace_id == ws, Company.contador.is_(False),
                                                   digits_cnae.startswith(CNAE_CONTADOR_PREFIXO))):
        c.contador = True
        marcadas += 1
    # domínios de email presentes em mais de 20 empresas distintas (evidência de email cadastral)
    dom = func.lower(func.split_part(Evidence.valor, "@", 2))
    rows = session.execute(
        select(dom, func.count(func.distinct(Evidence.entidade_id))).where(
            Evidence.workspace_id == ws, Evidence.entidade_tipo == "company", Evidence.campo == "email_cadastral")
        .group_by(dom).having(func.count(func.distinct(Evidence.entidade_id)) > CONTADOR_DOMINIOS_MIN)).all()
    dominios = {d for d, _ in rows if d and d not in DOMINIOS_GENERICOS}
    bloqueados = 0
    contatos = list(session.scalars(select(Contact).where(Contact.workspace_id == ws, Contact.email.is_not(None))))
    contador_ids = set(session.scalars(select(Company.id).where(Company.workspace_id == ws, Company.contador.is_(True))))
    for ct in contatos:
        motivo = None
        if domain_of(ct.email) in dominios:
            motivo = "contador: domínio presente em mais de 20 CNPJs"
        elif ct.company_id in contador_ids:
            motivo = "contador: empresa de contabilidade (CNAE 69)"
        if motivo and not ct.bloqueado_envio:
            ct.bloqueado_envio, ct.bloqueio_motivo = True, motivo
            bloqueados += 1
    if dominios:
        session.execute(update(Company).where(Company.workspace_id == ws, Company.dominio.in_(dominios)).values(contador=True))
    session.flush()
    return {"contadores": marcadas + len(dominios), "contatos_bloqueados": bloqueados, "dominios_contador": sorted(dominios)}
