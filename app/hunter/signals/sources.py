"""Fontes de sinais, cada uma atrás de uma interface simples (F11): trocar de fonte não muda o resto do sistema.

- JobSource: vagas. Padrão = páginas "trabalhe conosco" do próprio site e feeds RSS configurados.
  LinkedIn/Indeed/Catho: só via fornecedor licenciado (implementar outra JobSource quando houver contrato).
- NewsSource: Google News RSS (consulta pelo nome da empresa).
"""
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import quote_plus, urljoin, urlsplit

import httpx
from defusedxml import ElementTree as ET

from app.hunter.companies.crawler import normalize_site
from app.hunter.companies.fetch import SafeFetcher
from app.hunter.signals.classify import classify_headline, mentions_company

CAREER_HINT = re.compile(r"trabalhe[-_ ]?conosco|carreira|vagas|work[-_ ]?with[-_ ]?us|careers|jobs", re.I)
JOB_WORDS = re.compile(r"\b(analista|assistente|gerente|coordenador|desenvolvedor|vendedor|designer|auxiliar|"
                       r"estagi[aá]rio|consultor|atendente|motorista|operador|t[eé]cnico|engenheiro|supervisor)\b", re.I)
GOOGLE_NEWS = "https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"


@dataclass
class RawSignal:
    tipo: str
    titulo: str
    fonte: str
    url: str
    confianca: float
    polaridade: str = "positivo"
    valido_ate: datetime | None = None


class JobSource(Protocol):
    def find(self, nome: str, site: str | None) -> list[RawSignal]: ...


class NewsSource(Protocol):
    def find(self, nome: str, extras: list[str]) -> list[RawSignal]: ...


# ---------- vagas ----------
class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self.titles: list[str] = []
        self._href: str | None = None
        self._buf: list[str] = []
        self._in: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href") or ""
            self._buf = []
        elif tag in ("h1", "h2", "h3", "h4", "li"):
            self._in = tag
            self._buf = []

    def handle_data(self, data):
        self._buf.append(data)

    def handle_endtag(self, tag):
        txt = " ".join("".join(self._buf).split())
        if tag == "a" and self._href is not None:
            self.links.append((self._href, txt))
            self._href = None
        elif tag == self._in:
            if txt:
                self.titles.append(txt)
            self._in = None


class CareersPageJobSource:
    """Procura, no site da empresa, o link de 'Trabalhe conosco' e conta os cargos citados na página."""

    def __init__(self, fetcher: SafeFetcher, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.fetcher, self.now = fetcher, now

    def find(self, nome: str, site: str | None) -> list[RawSignal]:
        home = normalize_site(site or "")
        if not home:
            return []
        first = self.fetcher.get(home)
        if not first.ok:
            return []
        p = _Links()
        p.feed(first.text)
        host = urlsplit(home).netloc
        target = None
        for href, txt in p.links:
            full = urljoin(first.url, href)
            if urlsplit(full).netloc == host and (CAREER_HINT.search(href) or CAREER_HINT.search(txt)):
                target = full
                break
        if not target:
            return []
        page = self.fetcher.get(target)
        if not page.ok:
            return []
        q = _Links()
        q.feed(page.text)
        cargos = sorted({t for t in q.titles if JOB_WORDS.search(t) and len(t) <= 90})
        if not cargos:
            return []
        titulo = f"{len(cargos)} vaga(s) aberta(s) no site: " + "; ".join(cargos[:3])
        return [RawSignal("vaga", titulo[:300], "pagina_trabalhe_conosco", page.url, 0.7,
                          valido_ate=self.now() + timedelta(days=30))]


class RssJobSource:
    """Feeds RSS de vagas configurados (um modelo de URL com {q} para o nome da empresa)."""

    def __init__(self, http: httpx.Client, templates: list[str]):
        self.http, self.templates = http, templates

    def find(self, nome: str, site: str | None) -> list[RawSignal]:
        out: list[RawSignal] = []
        for tpl in self.templates:
            url = tpl.replace("{q}", quote_plus(nome))
            for title, link, _ in _read_feed(self.http, url):
                if mentions_company(title, [nome]):
                    out.append(RawSignal("vaga", title[:300], "rss_vagas", link, 0.6,
                                         valido_ate=datetime.now(timezone.utc) + timedelta(days=30)))
        return out


# ---------- notícias ----------
def _read_feed(http: httpx.Client, url: str) -> list[tuple[str, str, datetime | None]]:
    try:
        r = http.get(url, headers={"User-Agent": "ProspectorBot/1.0"}, timeout=15)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except (httpx.HTTPError, ET.ParseError, ValueError):
        return []
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate")
        when = None
        if pub:
            try:
                when = parsedate_to_datetime(pub)
            except (TypeError, ValueError):
                when = None
        if title and link.startswith(("http://", "https://")):
            items.append((title, link, when))
    return items


class GoogleNewsSource:
    def __init__(self, http: httpx.Client, max_age_days: int = 60,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.http, self.max_age, self.now = http, max_age_days, now

    def find(self, nome: str, extras: list[str]) -> list[RawSignal]:
        out: list[RawSignal] = []
        for title, link, when in _read_feed(self.http, GOOGLE_NEWS.format(q=quote_plus(f'"{nome}"')))[:30]:
            if not mentions_company(title, [nome, *extras]):
                continue
            hit = classify_headline(title)
            if not hit:
                continue
            tipo, pol, dias = hit
            base = when or self.now()
            if self.now() - base > timedelta(days=self.max_age):
                continue
            out.append(RawSignal(tipo, title[:300], "google_news", link, 0.65, pol, base + timedelta(days=dias)))
        return out


# ---------- Maps ----------
def maps_signal(nome: str, url: str, rating: float | None, total: int | None, antes: dict | None) -> RawSignal | None:
    """Atividade recente no Maps. 'antes' = {'rating':..., 'total':...} de uma leitura anterior (do cache)."""
    if rating is None or not total or not url:
        return None
    if antes and antes.get("total") is not None:
        novas = total - int(antes["total"])
        if novas >= 5 and rating >= 4.0:
            return RawSignal("maps_ativo", f"{novas} novas avaliações no Maps (nota {rating:.1f})", "google_maps", url, 0.6,
                             valido_ate=datetime.now(timezone.utc) + timedelta(days=30))
        if antes.get("rating") is not None and rating <= float(antes["rating"]) - 0.3:
            return RawSignal("maps_queda", f"Nota no Maps caiu de {float(antes['rating']):.1f} para {rating:.1f}", "google_maps",
                             url, 0.6, "negativo", datetime.now(timezone.utc) + timedelta(days=30))
    return None
