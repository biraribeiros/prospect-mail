"""Google Places (New) — Text Search. Só os campos necessários, cache de 30 dias, estimativa de custo antes de rodar."""
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.compliance import create_compliance_record
from app.core.config import Settings
from app.core.csvio import norm_domain
from app.core.models import ApiCache, Company, Evidence, IcpProfile
from app.core.provenance import record_evidence

URL = "https://places.googleapis.com/v1/places:searchText"
# Máscara mínima: pedir menos campos custa menos. nextPageToken precisa estar na máscara para paginar.
FIELD_MASK = ("places.id,places.displayName,places.formattedAddress,places.websiteUri,"
              "places.nationalPhoneNumber,places.googleMapsUri,places.businessStatus,nextPageToken")
FONTE = "google_places"
CONF = 0.8
ADDRESS_RE = re.compile(r",\s*([^,]+?)\s+-\s+([A-Z]{2})\s*(?:,|$)")


class PlacesError(RuntimeError):
    pass


@dataclass
class Estimate:
    consultas: int
    em_cache: int
    novas: int
    custo_min: float
    custo_max: float
    preco_por_mil: float
    max_paginas: int


def build_queries(icp: IcpProfile) -> list[str]:
    if not icp.termos_maps:
        raise PlacesError("Preencha os termos de busca no Google Maps no perfil (ex.: escritório de contabilidade).")
    if not icp.municipios and not icp.ufs:
        raise PlacesError("Informe pelo menos uma cidade ou estado no perfil, para a busca não ficar ampla demais.")
    places = [f"{m}, {icp.ufs[0]}" if len(icp.ufs) == 1 else m for m in icp.municipios] or list(icp.ufs)
    return [f"{t} em {p}" for t in icp.termos_maps for p in places]


def parse_address(formatted: str | None) -> tuple[str | None, str | None]:
    matches = ADDRESS_RE.findall(formatted or "")
    return (matches[-1][0].strip(), matches[-1][1]) if matches else (None, None)


def cache_key(query: str, token: str | None) -> str:
    return hashlib.sha256(json.dumps({"q": query, "t": token}, sort_keys=True).encode()).hexdigest()


def _cached(session: Session, ws: int, key: str) -> dict | None:
    row = session.scalar(select(ApiCache).where(ApiCache.workspace_id == ws, ApiCache.provedor == FONTE,
                                                ApiCache.chave == key, ApiCache.expira_em > datetime.now(timezone.utc)))
    return row.payload if row else None


def _store(session: Session, ws: int, key: str, payload: dict, days: int) -> None:
    exp = datetime.now(timezone.utc) + timedelta(days=days)
    row = session.scalar(select(ApiCache).where(ApiCache.workspace_id == ws, ApiCache.provedor == FONTE,
                                                ApiCache.chave == key))
    if row:
        row.payload, row.expira_em = payload, exp
    else:
        session.add(ApiCache(workspace_id=ws, provedor=FONTE, chave=key, payload=payload, expira_em=exp))
    session.flush()


def estimate(session: Session, ws: int, icp: IcpProfile, settings: Settings) -> Estimate:
    queries = build_queries(icp)
    cached = sum(1 for q in queries if _cached(session, ws, cache_key(q, None)) is not None)
    novas = len(queries) - cached
    per_call = settings.places_price_per_1000_usd / 1000
    return Estimate(len(queries), cached, novas, round(novas * per_call, 4),
                    round(novas * settings.places_max_pages * per_call, 4),
                    settings.places_price_per_1000_usd, settings.places_max_pages)


class PlacesClient:
    def __init__(self, api_key: str, http: httpx.Client | None = None):
        if not api_key:
            raise PlacesError("Chave do Google Places não configurada (GOOGLE_PLACES_API_KEY no .env).")
        self.api_key = api_key
        self._http = http or httpx.Client(timeout=30)

    def search(self, query: str, page_token: str | None = None) -> dict:
        body: dict = {"textQuery": query, "languageCode": "pt-BR", "regionCode": "BR", "pageSize": 20}
        if page_token:
            body["pageToken"] = page_token
        r = self._http.post(URL, json=body, headers={"X-Goog-Api-Key": self.api_key, "X-Goog-FieldMask": FIELD_MASK})
        if r.status_code in (400, 401, 403):
            raise PlacesError("O Google recusou a consulta (chave inválida, sem permissão ou cobrança desativada).")
        if r.status_code == 429:
            raise PlacesError("Limite de consultas do Google atingido. Tente mais tarde.")
        r.raise_for_status()
        return r.json()


def _find_company(session: Session, ws: int, place_id: str, dominio: str | None) -> Company | None:
    ev = session.scalar(select(Evidence).where(Evidence.workspace_id == ws, Evidence.entidade_tipo == "company",
                                               Evidence.campo == "google_place_id", Evidence.valor == place_id))
    if ev:
        return session.get(Company, ev.entidade_id)
    if dominio:
        return session.scalar(select(Company).where(Company.workspace_id == ws, Company.dominio == dominio))
    return None


def save_place(session: Session, ws: int, place: dict) -> str:
    """Grava/atualiza uma empresa vinda do Places. Devolve 'criada', 'atualizada' ou 'ignorada'."""
    pid = place.get("id")
    nome = (place.get("displayName") or {}).get("text")
    if not pid or not nome or place.get("businessStatus") in ("CLOSED_PERMANENTLY",):
        return "ignorada"
    site = place.get("websiteUri")
    dominio = norm_domain(site)
    municipio, uf = parse_address(place.get("formattedAddress"))
    phone = re.sub(r"\D", "", place.get("nationalPhoneNumber") or "") or None
    values = {"nome_fantasia": nome, "site": site, "dominio": dominio, "municipio": municipio, "uf": uf,
              "telefone": phone, "endereco": place.get("formattedAddress")}
    company = _find_company(session, ws, pid, dominio)
    status = "atualizada"
    if company is None:
        company = Company(workspace_id=ws, **{k: v for k, v in values.items() if v})
        session.add(company)
        session.flush()
        status = "criada"
    else:
        for k, v in values.items():
            if v and getattr(company, k) in (None, ""):
                setattr(company, k, v)
    url = place.get("googleMapsUri")
    for campo, valor in {"google_place_id": pid, **values}.items():
        if valor:
            record_evidence(session, workspace_id=ws, entity_type="company", entity_id=company.id, field=campo,
                            value=str(valor), source=FONTE, url=url, confidence=CONF)
    create_compliance_record(session, workspace_id=ws, entity_type="company", entity_id=company.id, origem=FONTE)
    return status


def collect(session: Session, ws: int, icp: IcpProfile, client: PlacesClient, settings: Settings,
            progress=None) -> dict:
    """Roda as consultas do perfil. Para sozinho ao atingir o limite de gasto por execução."""
    per_call = settings.places_price_per_1000_usd / 1000
    cost, calls, cached_calls = 0.0, 0, 0
    counts = {"criadas": 0, "atualizadas": 0, "ignoradas": 0}
    avisos: list[str] = []
    stop = False
    queries = build_queries(icp)
    for qi, query in enumerate(queries, start=1):
        token: str | None = None
        for _ in range(settings.places_max_pages):
            key = cache_key(query, token)
            data = _cached(session, ws, key)
            if data is None:
                if cost + per_call > settings.places_max_usd_per_run + 1e-9:
                    avisos.append(f"Limite de gasto por execução (US$ {settings.places_max_usd_per_run:.2f}) atingido.")
                    stop = True
                    break
                data = client.search(query, token)
                cost += per_call
                calls += 1
                _store(session, ws, key, data, settings.places_cache_days)
            else:
                cached_calls += 1
            for place in data.get("places", []):
                counts[{"criada": "criadas", "atualizada": "atualizadas", "ignorada": "ignoradas"}[
                    save_place(session, ws, place)]] += 1
            token = data.get("nextPageToken")
            if not token:
                break
        if progress:
            progress({"consultas_feitas": qi, "consultas_total": len(queries), **counts}, round(cost, 6))
        if stop:
            break
    return {"consultas_total": len(queries), "chamadas_pagas": calls, "chamadas_em_cache": cached_calls,
            **counts, "avisos": avisos, "custo_usd": round(cost, 6)}
