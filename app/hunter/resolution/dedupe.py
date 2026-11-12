"""Etapa 3: uma empresa = um registro. Vínculo por CNPJ → domínio → telefone → nome+cidade (pg_trgm ≥ 0,85).

Regra de ouro: duas empresas com CNPJs DIFERENTES nunca são mescladas (podem ser filiais ou homônimas).
A mesclagem guarda um retrato do registro absorvido e pode ser desfeita.
"""
import re
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.models import Company, CompanyMerge, Contact, Evidence, OpportunitySignal

SIMILARIDADE = 0.85
DOMINIOS_GENERICOS = {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "yahoo.com.br", "uol.com.br", "bol.com.br",
                      "terra.com.br", "icloud.com", "live.com", "msn.com", "wix.com", "wixsite.com", "facebook.com",
                      "instagram.com", "linkedin.com", "google.com", "sites.google.com", "linktr.ee"}
CAMPOS = ("cnpj", "razao_social", "nome_fantasia", "dominio", "site", "cnae_principal", "municipio", "uf", "porte",
          "data_abertura", "situacao_cadastral", "telefone", "endereco")


def digits(v: str | None) -> str:
    return re.sub(r"\D", "", v or "")


def _nome(c: Company) -> str:
    return (c.nome_fantasia or c.razao_social or "").strip().lower()


def compatible(a: Company, b: Company) -> bool:
    """CNPJs diferentes (ambos preenchidos) = empresas diferentes."""
    return not (a.cnpj and b.cnpj and a.cnpj != b.cnpj)


def find_pairs(session: Session, ws: int) -> list[tuple[int, int, str]]:
    """Pares (a, b, motivo) de empresas que parecem ser a mesma, em ordem de prioridade das regras."""
    base = (Company.workspace_id == ws, Company.merged_into_id.is_(None))
    comps = {c.id: c for c in session.scalars(select(Company).where(*base))}
    pairs: dict[tuple[int, int], str] = {}

    def add(ids: list[int], motivo: str) -> None:
        ids = sorted(ids)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = comps[ids[i]], comps[ids[j]]
                if compatible(a, b):
                    pairs.setdefault((a.id, b.id), motivo)

    by_dom: dict[str, list[int]] = {}
    by_tel: dict[str, list[int]] = {}
    for c in comps.values():
        d = (c.dominio or "").lower().removeprefix("www.")
        if d and d not in DOMINIOS_GENERICOS:
            by_dom.setdefault(d, []).append(c.id)
        t = digits(c.telefone)[-10:]
        if len(t) == 10:
            by_tel.setdefault(t, []).append(c.id)
    for ids in by_dom.values():
        if len(ids) > 1:
            add(ids, "dominio")
    for ids in by_tel.values():
        if len(ids) > 1:
            add(ids, "telefone")

    # nome + cidade com similaridade (pg_trgm)
    a, b = Company.__table__.alias("a"), Company.__table__.alias("b")
    def plain(col):  # minúsculas e sem acento (pg_trgm trata "ã" e "a" como letras diferentes)
        return func.translate(func.lower(func.coalesce(col.c.nome_fantasia, col.c.razao_social)),
                              "áàâãäéèêëíìîïóòôõöúùûüç", "aaaaaeeeeiiiiooooouuuuc")
    na, nb = plain(a), plain(b)
    stmt = (select(a.c.id, b.c.id).where(
        a.c.workspace_id == ws, b.c.workspace_id == ws, a.c.id < b.c.id, a.c.merged_into_id.is_(None),
        b.c.merged_into_id.is_(None), func.lower(a.c.municipio) == func.lower(b.c.municipio),
        func.lower(a.c.uf) == func.lower(b.c.uf), func.similarity(na, nb) >= SIMILARIDADE))
    for ia, ib in session.execute(stmt):
        if compatible(comps[ia], comps[ib]):
            pairs.setdefault((ia, ib), "nome_cidade")
    return [(x, y, m) for (x, y), m in pairs.items()]


def pick_survivor(session: Session, a: Company, b: Company) -> tuple[Company, Company]:
    """Fica quem tem CNPJ; empate: quem tem mais evidências; empate: o mais antigo."""
    def peso(c: Company) -> tuple:
        n = session.scalar(select(func.count()).select_from(Evidence).where(
            Evidence.entidade_tipo == "company", Evidence.entidade_id == c.id))
        return (1 if c.cnpj else 0, n, -c.id)
    return (a, b) if peso(a) >= peso(b) else (b, a)


def merge_companies(session: Session, survivor: Company, loser: Company, motivo: str) -> CompanyMerge:
    if survivor.workspace_id != loser.workspace_id or survivor.id == loser.id:
        raise ValueError("Mesclagem inválida.")
    if not compatible(survivor, loser):
        raise ValueError("Empresas com CNPJs diferentes não podem ser mescladas.")
    ws = survivor.workspace_id
    snap: dict = {"campos": {k: (str(getattr(loser, k)) if getattr(loser, k) is not None else None) for k in CAMPOS},
                  "survivor_antes": {}, "contatos": [], "evidencias": [], "sinais": [], "sinais_removidos": []}
    cnpj_loser = loser.cnpj
    if cnpj_loser and not survivor.cnpj:
        loser.cnpj = None  # o índice único exige liberar o CNPJ antes de copiá-lo
        session.flush()
    for k in CAMPOS:
        if getattr(survivor, k) in (None, "") and getattr(loser, k) not in (None, "") and k != "cnpj":
            snap["survivor_antes"][k] = None
            setattr(survivor, k, getattr(loser, k))
    if cnpj_loser and not survivor.cnpj:
        snap["survivor_antes"]["cnpj"] = None
        survivor.cnpj = cnpj_loser
    snap["cnpj_loser"] = cnpj_loser

    snap["contatos"] = list(session.scalars(select(Contact.id).where(Contact.company_id == loser.id)))
    if snap["contatos"]:
        session.execute(update(Contact).where(Contact.id.in_(snap["contatos"])).values(company_id=survivor.id))
    snap["evidencias"] = list(session.scalars(select(Evidence.id).where(
        Evidence.entidade_tipo == "company", Evidence.entidade_id == loser.id)))
    if snap["evidencias"]:
        session.execute(update(Evidence).where(Evidence.id.in_(snap["evidencias"])).values(entidade_id=survivor.id))
    keep = {(s.tipo, s.hash) for s in session.scalars(select(OpportunitySignal).where(
        OpportunitySignal.company_id == survivor.id))}
    for sg in session.scalars(select(OpportunitySignal).where(OpportunitySignal.company_id == loser.id)):
        if (sg.tipo, sg.hash) in keep:
            snap["sinais_removidos"].append(sg.id)
            session.execute(delete(OpportunitySignal).where(OpportunitySignal.id == sg.id))
        else:
            snap["sinais"].append(sg.id)
            sg.company_id = survivor.id
    loser.merged_into_id = survivor.id
    rec = CompanyMerge(workspace_id=ws, survivor_id=survivor.id, merged_id=loser.id, motivo=motivo, snapshot=snap)
    session.add(rec)
    session.flush()
    return rec


def undo_merge(session: Session, merge_id: int, ws: int) -> CompanyMerge:
    rec = session.get(CompanyMerge, merge_id)
    if rec is None or rec.workspace_id != ws:
        raise LookupError("Mesclagem não encontrada.")
    if rec.desfeito_em:
        raise ValueError("Esta mesclagem já foi desfeita.")
    survivor, loser = session.get(Company, rec.survivor_id), session.get(Company, rec.merged_id)
    snap = rec.snapshot
    if snap.get("cnpj_loser") and survivor.cnpj == snap["cnpj_loser"] and "cnpj" in snap["survivor_antes"]:
        survivor.cnpj = None
        session.flush()
        loser.cnpj = snap["cnpj_loser"]
    for k in snap["survivor_antes"]:
        if k != "cnpj":
            setattr(survivor, k, None)
    if snap["contatos"]:
        session.execute(update(Contact).where(Contact.id.in_(snap["contatos"])).values(company_id=loser.id))
    if snap["evidencias"]:
        session.execute(update(Evidence).where(Evidence.id.in_(snap["evidencias"])).values(entidade_id=loser.id))
    if snap["sinais"]:
        session.execute(update(OpportunitySignal).where(OpportunitySignal.id.in_(snap["sinais"])).values(company_id=loser.id))
    loser.merged_into_id = None
    rec.desfeito_em = datetime.now(timezone.utc)
    session.flush()
    return rec


def merge_all(session: Session, ws: int) -> dict:
    """Aplica todas as mesclagens encontradas (em cadeia: A→B e B→C viram tudo em um registro)."""
    feitas = {"dominio": 0, "telefone": 0, "nome_cidade": 0}
    for _ in range(5):  # a cada rodada novos pares podem surgir (campos preenchidos pela mesclagem)
        pares = find_pairs(session, ws)
        if not pares:
            break
        usadas: set[int] = set()
        for a_id, b_id, motivo in pares:
            if a_id in usadas or b_id in usadas:
                continue
            a, b = session.get(Company, a_id), session.get(Company, b_id)
            if a.merged_into_id or b.merged_into_id or not compatible(a, b):
                continue
            surv, lose = pick_survivor(session, a, b)
            merge_companies(session, surv, lose, motivo)
            usadas.update({a_id, b_id})
            feitas[motivo] += 1
    return feitas
