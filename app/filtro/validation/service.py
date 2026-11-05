"""Etapa 4: valida emails em camadas e grava o resultado com cache (email 90 dias; domínio catch-all 7 dias)."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.models import ApiCache, Contact, Suppression, Verification
from app.filtro.validation.layers import (DnsResolver, LayerFail, Resolver, check_disposable, check_mx, check_role,
                                          check_syntax)
from app.filtro.validation.verifiers import EmailVerifier, VerifierError, VerifierResult, build_verifier
from app.hunter.companies.service import EXPECTED_ERRORS, run_tracked  # noqa: F401  (run_tracked reaproveitado)

STATUS = ("valido", "arriscado", "invalido", "bloqueado")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- cache ----------
def catchall_cached(session: Session, ws: int, domain: str) -> bool | None:
    row = session.scalar(select(ApiCache).where(ApiCache.workspace_id == ws, ApiCache.provedor == "catchall",
                                                ApiCache.chave == domain, ApiCache.expira_em > _now()))
    return None if row is None else bool(row.payload.get("catch_all"))


def catchall_store(session: Session, ws: int, domain: str, value: bool, days: int) -> None:
    row = session.scalar(select(ApiCache).where(ApiCache.workspace_id == ws, ApiCache.provedor == "catchall",
                                                ApiCache.chave == domain))
    exp = _now() + timedelta(days=days)
    if row:
        row.payload, row.expira_em, row.criado_em = {"catch_all": value}, exp, _now()
    else:
        session.add(ApiCache(workspace_id=ws, provedor="catchall", chave=domain, payload={"catch_all": value}, expira_em=exp))
    session.flush()


def pending_contacts(session: Session, ws: int, limit: int, days: int, force: bool = False) -> list[Contact]:
    """Contatos com email que nunca foram verificados ou cuja verificação passou de 'days' dias."""
    stmt = select(Contact).where(Contact.workspace_id == ws, Contact.email.is_not(None), Contact.bloqueado_envio.is_(False))
    if not force:
        stmt = stmt.where(or_(Contact.verificado_em.is_(None), Contact.verificado_em < _now() - timedelta(days=days)))
    return list(session.scalars(stmt.order_by(Contact.id).limit(limit)))


# ---------- decisão de um email ----------
def local_layers(session: Session, ws: int, email: str, resolver: Resolver, verifier: EmailVerifier | None,
                 suppressed: set[str]) -> tuple[str | None, tuple[str, str, str] | None]:
    """Camadas sem custo, na ordem, parando na primeira reprovação. Devolve (email_normalizado, (status, camada, motivo))
    — o segundo item é None quando o email ainda precisa do verificador SMTP."""
    norm, fail = check_syntax(email)
    if fail:
        return None, (fail.status, fail.camada, fail.motivo)
    dom = norm.rsplit("@", 1)[1]
    if norm in suppressed or dom in suppressed:
        return norm, ("bloqueado", "supressao", "está na lista de supressão")
    f = check_disposable(norm) or check_role(norm) or check_mx(norm, resolver)
    if f:
        return norm, (f.status, f.camada, f.motivo)
    if catchall_cached(session, ws, dom):  # domínio aceita qualquer endereço: não adianta pagar por cada um
        return norm, ("arriscado", "catch_all", "o domínio aceita qualquer endereço (catch-all)")
    if verifier is None:
        return norm, ("arriscado", "sem_verificador", "passou nas checagens locais; falta um verificador SMTP")
    return norm, None


def smtp_outcome(session: Session, ws: int, norm: str, res: VerifierResult, catch_days: int) -> tuple[str, str, str]:
    dom = norm.rsplit("@", 1)[1]
    if res.catch_all:
        catchall_store(session, ws, dom, True, catch_days)
        return "arriscado", "catch_all", "o domínio aceita qualquer endereço (catch-all)"
    if res.status == "valido":
        catchall_store(session, ws, dom, False, catch_days)
        return "valido", "smtp", res.detalhe
    if res.status == "invalido":
        return "invalido", "smtp", res.detalhe or "o servidor recusou o endereço"
    return "arriscado", "smtp", res.detalhe or "o servidor não deu resposta conclusiva"


def decide(session: Session, ws: int, email: str, resolver: Resolver, verifier: EmailVerifier | None,
           catch_days: int, suppressed: set[str]) -> tuple[str, str, str, VerifierResult | None]:
    """Decisão completa de um email (camadas locais + provedor). Devolve (status, camada, motivo, resultado_do_provedor)."""
    norm, local = local_layers(session, ws, email, resolver, verifier, suppressed)
    if local:
        return (*local, None)
    res = verifier.verify(norm)  # type: ignore[union-attr]  (local é None só quando há verificador)
    return (*smtp_outcome(session, ws, norm, res, catch_days), res)


def apply_result(session: Session, ws: int, contact: Contact, status: str, camada: str, motivo: str,
                 res: VerifierResult | None, provedor: str | None) -> None:
    contact.email_status = status
    contact.verificado_em = _now()
    session.add(Verification(workspace_id=ws, contact_id=contact.id, camada=camada, resultado=status,
                             detalhes={"motivo": motivo, **({"provedor": res.raw} if res else {})}, provedor=provedor))


def run_validation(session: Session, ws: int, limit: int = 500, force: bool = False, resolver: Resolver | None = None,
                   verifier: EmailVerifier | None = None, use_default_verifier: bool = True) -> dict:
    s = get_settings()
    targets = pending_contacts(session, ws, limit, s.verifier_cache_days, force)
    ids = [c.id for c in targets]
    if verifier is None and use_default_verifier:
        verifier, _aviso = build_verifier(s.verifier_provider, s.verifier_api_key, s.reacher_url)
    estado = [[c.id, c.verificado_em.isoformat() if c.verificado_em else None] for c in targets]
    params = {"ids": hashlib.sha256(json.dumps(estado).encode()).hexdigest()[:16], "n": len(ids),
              "dia": _now().date().isoformat(), "prov": verifier.nome if verifier else "nenhum", "force": force}
    if not ids:
        return {"job_id": None, "repetido": False, "verificados": 0, "aviso": "Nenhum email pendente de verificação."}
    resolver = resolver or DnsResolver()
    suppressed = {x.email_ou_dominio.lower() for x in session.scalars(select(Suppression).where(Suppression.workspace_id == ws))}

    def work(progress):
        tot = {"verificados": 0, "valido": 0, "arriscado": 0, "invalido": 0, "bloqueado": 0, "falhas_provedor": 0,
               "custo_usd": 0.0}
        # etapa 1 (sequencial, barata): camadas locais; etapa 2: chamadas ao provedor com no máximo N em paralelo
        pend_api: list[tuple[Contact, str]] = []
        for i, c in enumerate(targets, start=1):
            norm, local = local_layers(session, ws, c.email, resolver, verifier, suppressed)
            if local:
                apply_result(session, ws, c, *local, None, None)
                tot[local[0]] += 1
                tot["verificados"] += 1
            else:
                pend_api.append((c, norm))
            if i % 50 == 0:
                session.commit()
                progress({**tot, "total": len(targets)})
        if pend_api and verifier is not None:
            def call(item):
                try:
                    return verifier.verify(item[1])
                except VerifierError as e:
                    return e
            with ThreadPoolExecutor(max_workers=max(1, min(s.verifier_max_parallel, 3))) as pool:
                outs = list(pool.map(call, pend_api))
            for (c, norm), res in zip(pend_api, outs):
                if isinstance(res, VerifierError):
                    tot["falhas_provedor"] += 1  # fica sem verificar: tenta de novo na próxima rodada
                    continue
                st, cam, mot = smtp_outcome(session, ws, norm, res, s.verifier_catchall_days)
                apply_result(session, ws, c, st, cam, mot, res, verifier.nome)
                tot[st] += 1
                tot["verificados"] += 1
                tot["custo_usd"] = round(tot["custo_usd"] + res.custo_usd, 6)
        return tot

    return run_tracked(session, ws, "validacao_emails", params, work)


def distribution(session: Session, ws: int) -> dict[str, int]:
    from sqlalchemy import func

    rows = dict(session.execute(select(Contact.email_status, func.count()).where(
        Contact.workspace_id == ws, Contact.email.is_not(None)).group_by(Contact.email_status)).all())
    return {k: rows.get(k, 0) for k in ("valido", "arriscado", "invalido", "bloqueado", "nao_verificado")}


__all__ = ["run_validation", "decide", "distribution", "pending_contacts", "LayerFail"]
