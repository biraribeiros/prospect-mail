"""Extração de contatos de páginas públicas: emails (inclusive escondidos), telefones, WhatsApp, CNPJ, redes."""
import html
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urljoin, urlsplit

from app.core.csvio import cnpj_valido
from app.hunter.mailbox.parse import EMAIL_RE
from app.hunter.mailbox.signature import PHONE_RE

MAILTO_RE = re.compile(r'href\s*=\s*["\']mailto:([^"\'?>\s]+)', re.I)
TEL_RE = re.compile(r'href\s*=\s*["\']tel:([^"\'>\s]+)', re.I)
EMAIL_TEXT_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
OBFUSCATED_RE = re.compile(
    r"([A-Za-z0-9._%+-]+)\s*[\[(]\s*(?:at|arroba|@)\s*[\])]\s*"
    r"([A-Za-z0-9-]+(?:(?:\s*[\[(]\s*(?:dot|ponto)\s*[\])]\s*|\.)[A-Za-z0-9-]+)+)", re.I)
CFEMAIL_RE = re.compile(r'data-cfemail\s*=\s*["\']([0-9a-fA-F]+)["\']')
WHATSAPP_RE = re.compile(r"(?:wa\.me/|api\.whatsapp\.com/send\?[^\"'\s>]*phone=|whatsapp://send\?[^\"'\s>]*phone=)\+?(\d{10,15})", re.I)
CNPJ_RE = re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)")
HREF_RE = re.compile(r'<a\b[^>]*?href\s*=\s*["\']([^"\'#>\s][^"\'>]*)["\'][^>]*>(.*?)</a>', re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style|noscript).*?>.*?</\1>")
SOCIALS = {
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9_.]+/?", re.I),
    "facebook": re.compile(r"https?://(?:www\.)?facebook\.com/[A-Za-z0-9_.\-]+/?", re.I),
    "linkedin": re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in)/[A-Za-z0-9_.%\-]+/?", re.I),
    "youtube": re.compile(r"https?://(?:www\.)?youtube\.com/(?:@|c/|channel/|user/)[A-Za-z0-9_.\-]+", re.I),
    "tiktok": re.compile(r"https?://(?:www\.)?tiktok\.com/@[A-Za-z0-9_.]+", re.I),
    "x": re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/[A-Za-z0-9_]+/?", re.I),
}
FAKE_EMAIL_PARTS = ("example.", "exemplo.", "seu@", "seuemail", "email@email", "nome@", "usuario@", "user@domain",
                    "sentry", "wixpress", "domain.com", "@2x", "@3x", "yourdomain", "seudominio", "test@")
ASSET_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js", ".ico", ".woff", ".woff2", ".mp4")
PAGE_HINTS = ("contato", "contact", "sobre", "about", "equipe", "team", "quem-somos", "quemsomos", "fale-conosco",
              "faleconosco", "institucional", "atendimento", "trabalhe")
DEFAULT_PATHS = ("/contato", "/sobre", "/equipe", "/fale-conosco", "/quem-somos")


@dataclass
class PageInfo:
    emails: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    whatsapps: set[str] = field(default_factory=set)
    cnpjs: set[str] = field(default_factory=set)
    socials: dict[str, str] = field(default_factory=dict)
    links: list[str] = field(default_factory=list)
    text_len: int = 0
    needs_js: bool = False


def valid_email(addr: str) -> str | None:
    addr = addr.strip().strip(".,;:()<>\"'").lower()
    if not EMAIL_RE.match(addr) or addr.endswith(ASSET_EXT) or any(p in addr for p in FAKE_EMAIL_PARTS):
        return None
    tld = addr.rsplit(".", 1)[-1]
    return addr if tld.isalpha() and 2 <= len(tld) <= 24 else None


def decode_cfemail(hexstr: str) -> str | None:
    """Cloudflare esconde emails em data-cfemail: o primeiro byte é a chave do XOR."""
    try:
        data = bytes.fromhex(hexstr)
        return bytes(b ^ data[0] for b in data[1:]).decode("utf-8", "replace")
    except (ValueError, IndexError):
        return None


def visible_text(raw_html: str) -> str:
    text = TAG_RE.sub(" ", re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", SCRIPT_STYLE_RE.sub(" ", raw_html)))
    return html.unescape(text)


def extract(raw_html: str, base_url: str) -> PageInfo:
    info = PageInfo()
    doc = html.unescape(raw_html)  # &#64; (@) e afins
    text = visible_text(raw_html)
    info.text_len = len(re.sub(r"\s+", " ", text).strip())
    scripts = len(re.findall(r"<script\b", raw_html, re.I))
    info.needs_js = info.text_len < 200 and (scripts >= 3 or bool(re.search(r'id=["\'](?:root|__next|app)["\']', raw_html)))

    candidates = [unquote(m) for m in MAILTO_RE.findall(doc)]
    candidates += EMAIL_TEXT_RE.findall(text)
    for m in OBFUSCATED_RE.finditer(text):
        dom = re.sub(r"\s*[\[(]\s*(?:dot|ponto)\s*[\])]\s*", ".", m.group(2), flags=re.I)
        candidates.append(f"{m.group(1)}@{re.sub(r'\s+', '', dom)}")
    for hx in CFEMAIL_RE.findall(raw_html):
        dec = decode_cfemail(hx)
        if dec:
            candidates.append(dec)
    for c in candidates:
        for part in re.split(r"[,;]", c):
            v = valid_email(part)
            if v:
                info.emails.add(v)

    for raw in TEL_RE.findall(doc) + [m.group() for m in PHONE_RE.finditer(text)]:
        digits = re.sub(r"\D", "", unquote(raw))
        if 10 <= len(digits) <= 13:
            info.phones.add(digits)
    info.whatsapps = {m for m in WHATSAPP_RE.findall(doc)}
    for m in CNPJ_RE.findall(text):
        d = re.sub(r"\D", "", m)
        if cnpj_valido(d):
            info.cnpjs.add(d)
    for net, rx in SOCIALS.items():
        m = rx.search(doc)
        if m:
            info.socials[net] = m.group().rstrip("/")
    host = urlsplit(base_url).hostname or ""
    for href, label in HREF_RE.findall(raw_html):
        absu = urljoin(base_url, html.unescape(href))
        p = urlsplit(absu)
        if p.scheme in ("http", "https") and (p.hostname or "").removeprefix("www.") == host.removeprefix("www."):
            blob = f"{p.path} {TAG_RE.sub('', label)}".lower()
            if any(h in blob for h in PAGE_HINTS):
                info.links.append(p._replace(fragment="", query="").geturl())
    info.links = list(dict.fromkeys(info.links))
    return info
