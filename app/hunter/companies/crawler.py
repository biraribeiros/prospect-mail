"""Lê o site de uma empresa: home + páginas de contato/sobre/equipe. Só HTTP simples (sem navegador)."""
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from app.hunter.companies.extract import DEFAULT_PATHS, extract
from app.hunter.companies.fetch import SafeFetcher


@dataclass
class CrawlResult:
    status: str = "erro"  # ok | bloqueado_robots | bloqueado_rede | precisa_js | inalcancavel | erro
    paginas_lidas: int = 0
    emails: dict[str, str] = field(default_factory=dict)  # email -> URL da página onde apareceu
    telefones: dict[str, str] = field(default_factory=dict)
    whatsapps: dict[str, str] = field(default_factory=dict)
    cnpjs: dict[str, str] = field(default_factory=dict)
    redes: dict[str, str] = field(default_factory=dict)


def normalize_site(site: str) -> str | None:
    s = (site or "").strip()
    if not s:
        return None
    if "://" not in s:
        s = "https://" + s
    p = urlsplit(s)
    return f"{p.scheme}://{p.netloc}/" if p.netloc else None


def crawl_site(fetcher: SafeFetcher, site: str, max_pages: int = 5) -> CrawlResult:
    res = CrawlResult()
    home = normalize_site(site)
    if not home:
        res.status = "erro"
        return res
    first = fetcher.get(home)
    if first.error in ("bloqueado_robots", "bloqueado_rede"):
        res.status = first.error
        return res
    if not first.ok:
        res.status = "inalcancavel"
        return res

    pages = [(first.url, extract(first.text, first.url))]
    queue: list[str] = []
    for link in pages[0][1].links:
        queue.append(link)
    queue += [urljoin(first.url, p) for p in DEFAULT_PATHS]
    seen = {first.url.rstrip("/")}
    for url in queue:
        if len(pages) >= max_pages:
            break
        if url.rstrip("/") in seen:
            continue
        seen.add(url.rstrip("/"))
        r = fetcher.get(url)
        if r.error == "bloqueado_robots":
            continue
        if r.ok:
            pages.append((r.url, extract(r.text, r.url)))

    for url, info in pages:
        for e in info.emails:
            res.emails.setdefault(e, url)
        for t in info.phones:
            res.telefones.setdefault(t, url)
        for w in info.whatsapps:
            res.whatsapps.setdefault(w, url)
        for c in info.cnpjs:
            res.cnpjs.setdefault(c, url)
        for net, u in info.socials.items():
            res.redes.setdefault(net, u)
    res.paginas_lidas = len(pages)
    achou = bool(res.emails or res.telefones or res.whatsapps or res.cnpjs or res.redes)
    res.status = "precisa_js" if (not achou and any(i.needs_js for _, i in pages)) else "ok"
    return res
