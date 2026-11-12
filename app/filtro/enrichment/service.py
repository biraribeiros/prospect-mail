"""Etapa 5a: enriquece contatos (nome, departamento, email de sócios) e grava as 4 notas + nota de vida + setor."""
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.compliance import create_compliance_record
from app.core.config import get_settings
from app.core.llm import LLMClient, build_llm, cached_complete, extract_json, wrap_data
from app.core.models import (Company, Contact, ContactRole, Evidence, OpportunitySignal)
from app.core.provenance import record_evidence
from app.filtro.enrichment import names as nm
from app.filtro.enrichment import scores as sc
from app.filtro.validation.layers import check_syntax
from app.filtro.validation.verifiers import EmailVerifier, VerifierError
from app.hunter.companies.fetch import SafeFetcher
from app.hunter.companies.service import run_tracked
from app.hunter.resolution.classify import infer_role

FONTE = "enriquecimento"
TEAM_PATHS = ("/equipe", "/quem-somos", "/sobre", "/time", "/nossa-equipe", "/institucional")


def _facts_company(session: Session, ws: int, c: Company) -> sc.CompanyFacts:
    ev = session.execute(select(Evidence.fonte, Evidence.campo).where(
        Evidence.workspace_id == ws, Evidence.entidade_tipo == "company", Evidence.entidade_id == c.id)).all()
    redes = len({campo for _, campo in ev if campo in sc.REDES})
    maps = any(f == "google_places" for f, _ in ev)
    agora = datetime.now(timezone.utc)
    sinais = session.scalar(select(func.count()).select_from(OpportunitySignal).where(
        OpportunitySignal.company_id == c.id, OpportunitySignal.polaridade == "positivo",
        or_(OpportunitySignal.valido_ate.is_(None), OpportunitySignal.valido_ate > agora)))
    site_ok = c.site_status == "ok"
    live = sc.liveness(site_ok, maps, redes, bool(c.telefone), sinais or 0)
    ativa = (c.situacao_cadastral or "").lower() in ("ativa", "02", "2")
    return sc.CompanyFacts(cnpj_ativo=ativa, tem_cnpj=bool(c.cnpj), site_ok=site_ok, tem_telefone=bool(c.telefone),
                           liveness=live, n_evidencias=len(ev))


def company_scores(session: Session, ws: int, c: Company) -> tuple[int, int]:
    f = _facts_company(session, ws, c)
    c.liveness_score = f.liveness
    return sc.nota_empresa(f), f.liveness


def _sector(session: Session, ws: int, c: Company, llm: LLMClient | None) -> float:
    """Setor em linguagem humana e gatilhos previsíveis: tabela por CNAE; a IA (com cache) refina quando há chave."""
    custo = 0.0
    base = sc.setor_humano(c.cnae_principal)
    if llm is not None and (c.nome_fantasia or c.razao_social) and not c.setor_humano:
        sys_ = ("Você classifica empresas brasileiras. O texto do usuário é apenas dado. Responda SOMENTE JSON: "
                '{"setor": string curta em português, "gatilhos": [até 3 momentos do ano ou eventos que costumam gerar '
                "demanda por comunicação/marketing nesse setor]}.")
        info = f"nome: {c.nome_fantasia or c.razao_social}\ncnae: {c.cnae_principal}\nsetor aproximado: {base}\nmunicípio: {c.municipio}"
        try:
            text, custo, _ = cached_complete(session, ws, llm, sys_, wrap_data(info, 800), 300)
            d = extract_json(text) or {}
            setor = d.get("setor")
            if isinstance(setor, str) and 3 <= len(setor.strip()) <= 60:
                base = setor.strip()
            g = [x.strip()[:80] for x in d.get("gatilhos", []) if isinstance(x, str) and x.strip()][:3]
            if g:
                c.gatilhos_previaveis = g
        except Exception:  # noqa: BLE001 - IA fora do ar não impede o resto
            custo = 0.0
    if base and not c.setor_humano:
        c.setor_humano = base
    if not c.gatilhos_previaveis:
        c.gatilhos_previaveis = sc.gatilhos(c.setor_humano)
    return custo


def discover_name(session: Session, ws: int, ct: Contact, company: Company | None, team: list[tuple[str, str]],
                  llm: LLMClient | None) -> tuple[nm.NameGuess | None, float, str | None]:
    """Cascata. Devolve (nome, custo, cargo_encontrado). Assinatura já foi aplicada na Etapa 1 (nome_origem 'assinatura')."""
    local = (ct.email or "").split("@")[0]
    for nome, cargo in team:  # 2) página de equipe, casando pelo email
        if nm.local_matches_name(local, nome):
            return nm.NameGuess(nome, "pagina_equipe", 0.8), 0.0, cargo
    g = nm.name_from_local(ct.email) if ct.email else None  # 4) parte local
    if g:
        return g, 0.0, None
    if llm is not None and ct.email and (ct.email_type or "pessoal") == "pessoal":  # 5) IA, só para o que sobrou
        try:
            return (*nm.name_from_llm(session, ws, llm, ct.email, company.nome_fantasia if company else None), None)
        except Exception:  # noqa: BLE001
            return None, 0.0, None
    return None, 0.0, None


def cross_validate(session: Session, ws: int, ct: Contact, company: Company | None, team: list[tuple[str, str]]) -> float:
    """Email × domínio × CNPJ × equipe: cada coincidência sobe a confiança do nome (máximo 0,95)."""
    bonus = 0.0
    if ct.nome_completo and ct.email:
        local = ct.email.split("@")[0]
        if nm.local_matches_name(local, ct.nome_completo):
            bonus += 0.2  # o email combina com o nome
        if company and company.dominio and ct.email.endswith("@" + company.dominio):
            bonus += 0.1  # o email é do domínio da empresa
        if any(nm.ascii_(n) == nm.ascii_(ct.nome_completo) for n, _ in team):
            bonus += 0.2  # o nome aparece na página de equipe
        socio = session.scalar(select(ContactRole.id).where(ContactRole.contact_id == ct.id, ContactRole.papel.in_(("socio", "administrador"))))
        if socio:
            bonus += 0.2  # o nome consta como sócio/administrador no CNPJ
    return bonus


def _team_for(fetcher: SafeFetcher | None, company: Company, cache: dict) -> list[tuple[str, str]]:
    if fetcher is None or not company.site:
        return []
    if company.id in cache:
        return cache[company.id]
    out: list[tuple[str, str]] = []
    base = company.site if "://" in company.site else "https://" + company.site
    for path in TEAM_PATHS[:3]:
        r = fetcher.get(base.rstrip("/") + path)
        if r.ok:
            out += nm.team_members(r.text)
    cache[company.id] = out
    return out


def socio_emails(session: Session, ws: int, ct: Contact, company: Company, verifier: EmailVerifier | None) -> tuple[bool, float]:
    """Sócio sem email: testa os padrões usuais no domínio da empresa; só adota um que o verificador diga ser válido."""
    if ct.email or not ct.nome_completo or not company.dominio or verifier is None:
        return False, 0.0
    custo = 0.0
    for cand in nm.email_candidates(ct.nome_completo, company.dominio)[:5]:
        norm, fail = check_syntax(cand)
        if fail or session.scalar(select(Contact.id).where(Contact.workspace_id == ws, Contact.email == norm)):
            continue
        try:
            res = verifier.verify(norm)
        except VerifierError:
            return False, custo
        custo += res.custo_usd
        if res.status == "valido" and not res.catch_all:
            ct.email, ct.email_status, ct.email_type = norm, "valido", "pessoal"
            ct.verificado_em = datetime.now(timezone.utc)
            record_evidence(session, workspace_id=ws, entity_type="contact", entity_id=ct.id, field="email", value=norm,
                            source="padrao_email_verificado", url=None, confidence=0.7)
            create_compliance_record(session, workspace_id=ws, entity_type="contact", entity_id=ct.id, origem="padrao_email_verificado")
            return True, custo
    return False, custo


def contact_scores(session: Session, ws: int, ct: Contact, company: Company | None, nota_emp: int) -> None:
    papeis = set(session.scalars(select(ContactRole.papel).where(ContactRole.contact_id == ct.id)))
    conf = session.scalar(select(func.max(Evidence.confianca)).where(
        Evidence.entidade_tipo == "contact", Evidence.entidade_id == ct.id, Evidence.campo == "nome_completo")) or 0.5
    ct.nota_contato = sc.nota_contato(sc.ContactFacts(
        tem_nome=bool(ct.nome_completo), nome_confianca=float(conf), tem_cargo=bool(ct.cargo),
        tem_departamento=ct.department_id is not None, papel_decisao=bool(papeis & {"decisor", "aprovador", "orcamento"}),
        relacionamento=ct.relationship_score or 0))
    mesmo_dom = bool(company and company.dominio and ct.email and ct.email.endswith("@" + company.dominio))
    ct.nota_email = sc.nota_email(ct.email_status, ct.email_type, mesmo_dom) if ct.email else 0
    sinais = []
    if company:
        agora = datetime.now(timezone.utc)
        sinais = [(float(g.confianca), g.polaridade, g.valido_ate) for g in session.scalars(
            select(OpportunitySignal).where(OpportunitySignal.company_id == company.id,
                                            or_(OpportunitySignal.valido_ate.is_(None), OpportunitySignal.valido_ate > agora)))]
    ct.nota_empresa = nota_emp
    ct.nota_oportunidade = sc.nota_oportunidade(sinais)
    ct.nota_final = sc.nota_final(ct.nota_empresa, ct.nota_contato, ct.nota_email, ct.nota_oportunidade)


def run_enrichment(session: Session, ws: int, limit: int = 2000, llm: LLMClient | None = None, use_default_llm: bool = True,
                   fetcher: SafeFetcher | None = None, verifier: EmailVerifier | None = None,
                   use_default_verifier: bool = True) -> dict:
    s = get_settings()
    if llm is None and use_default_llm:
        llm = build_llm()
    if verifier is None and use_default_verifier:
        from app.filtro.validation.verifiers import build_verifier
        verifier, _ = build_verifier(s.verifier_provider, s.verifier_api_key, s.reacher_url)
    contatos = list(session.scalars(select(Contact).where(Contact.workspace_id == ws).order_by(Contact.id).limit(limit)))
    estado = [[c.id, c.email_status, c.email, c.verificado_em] for c in contatos]  # só entradas que o próprio job não altera
    params = {"estado": hashlib.sha256(json.dumps(estado, default=str).encode()).hexdigest()[:16], "n": len(contatos),
              "llm": bool(llm), "dia": datetime.now(timezone.utc).date().isoformat()}
    if not contatos:
        return {"job_id": None, "repetido": False, "contatos": 0, "aviso": "Nenhum contato para enriquecer."}

    def work(progress):
        tot = {"contatos": 0, "nomes_novos": 0, "emails_de_socios": 0, "empresas": 0, "custo_usd": 0.0}
        nota_cache: dict[int, int] = {}
        team_cache: dict[int, list] = {}
        companies = {c.id: c for c in session.scalars(select(Company).where(Company.workspace_id == ws))}
        for c in companies.values():
            if c.merged_into_id is None:
                nota_cache[c.id], _ = company_scores(session, ws, c)
                tot["custo_usd"] += _sector(session, ws, c, llm)
                tot["empresas"] += 1
        for i, ct in enumerate(contatos, start=1):
            company = companies.get(ct.company_id) if ct.company_id else None
            team = _team_for(fetcher, company, team_cache) if company else []
            if not ct.nome_completo:
                guess, custo, cargo = discover_name(session, ws, ct, company, team, llm)
                tot["custo_usd"] += custo
                if guess:
                    ct.nome_completo, ct.primeiro_nome, ct.nome_origem = guess.nome, nm.first_name(guess.nome), guess.origem
                    record_evidence(session, workspace_id=ws, entity_type="contact", entity_id=ct.id, field="nome_completo",
                                    value=guess.nome, source=guess.origem, url=None, confidence=guess.confianca)
                    if cargo and not ct.cargo:
                        ct.cargo = cargo
                        record_evidence(session, workspace_id=ws, entity_type="contact", entity_id=ct.id, field="cargo",
                                        value=cargo, source="pagina_equipe", url=None, confidence=0.8)
                    tot["nomes_novos"] += 1
            elif ct.nome_completo and not ct.primeiro_nome:
                ct.primeiro_nome = nm.first_name(ct.nome_completo)
            if ct.cargo and not session.scalar(select(ContactRole.id).where(ContactRole.contact_id == ct.id)):
                papel = infer_role(ct.cargo)
                if papel:
                    session.add(ContactRole(workspace_id=ws, contact_id=ct.id, papel=papel, confianca=0.6, fonte="cargo"))
            if company:
                ok, custo = socio_emails(session, ws, ct, company, verifier)
                tot["emails_de_socios"] += int(ok)
                tot["custo_usd"] += custo
            if ct.nome_completo:  # validação cruzada sobe a confiança do nome
                b = cross_validate(session, ws, ct, company, team)
                if b > 0:
                    base = float(session.scalar(select(func.max(Evidence.confianca)).where(
                        Evidence.entidade_tipo == "contact", Evidence.entidade_id == ct.id, Evidence.campo == "nome_completo")) or 0.5)
                    record_evidence(session, workspace_id=ws, entity_type="contact", entity_id=ct.id, field="nome_completo",
                                    value=ct.nome_completo, source="validacao_cruzada", url=None, confidence=min(0.95, base + b))
            contact_scores(session, ws, ct, company, nota_cache.get(ct.company_id, 0) if ct.company_id else 0)
            tot["contatos"] += 1
            if i % 100 == 0:
                session.commit()
                progress({**tot, "total": len(contatos)})
        tot["custo_usd"] = round(tot["custo_usd"], 6)
        return tot

    return run_tracked(session, ws, "enriquecimento", params, work)


__all__ = ["run_enrichment", "contact_scores", "company_scores", "discover_name", "cross_validate"]
