"""Busca de páginas públicas com educação: robots.txt, 1 requisição/s por domínio, timeout, limite de tamanho,
redirecionamentos conferidos um a um contra SSRF."""
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.core.netsafe import check_url

MAX_BYTES = 2_000_000
MAX_REDIRECTS = 3
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")


@dataclass
class FetchResult:
    url: str
    status: int | None = None
    text: str = ""
    error: str | None = None  # bloqueado_robots | bloqueado_rede | tipo_nao_suportado | erro_http | timeout | erro_conexao

    @property
    def ok(self) -> bool:
        return self.error is None and self.status is not None and 200 <= self.status < 300


class SafeFetcher:
    def __init__(self, http: httpx.Client | None = None, user_agent: str = "ProspectorBot/1.0",
                 delay: float = 1.0, timeout: float = 10.0, url_check: Callable[[str], str | None] = check_url,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self._http = http or httpx.Client(timeout=timeout, follow_redirects=False)
        self.ua, self.delay, self._check, self._sleep, self._clock = user_agent, delay, url_check, sleep, clock
        self._last: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}

    # --- ritmo: no máximo 1 requisição por intervalo, por domínio ---
    def _wait(self, host: str, delay: float) -> None:
        now = self._clock()
        last = self._last.get(host)
        if last is not None and now - last < delay:
            self._sleep(delay - (now - last))
        self._last[host] = self._clock()

    def _request(self, url: str, host_delay: float) -> tuple[httpx.Response | None, str | None, str]:
        """GET com redirecionamentos manuais. Devolve (resposta, erro, url_final)."""
        for _ in range(MAX_REDIRECTS + 1):
            blocked = self._check(url)
            if blocked:
                return None, "bloqueado_rede", url
            self._wait(urlsplit(url).hostname or "", host_delay)
            try:
                req = self._http.build_request("GET", url, headers={"User-Agent": self.ua, "Accept": "text/html,*/*;q=0.5"})
                resp = self._http.send(req, stream=True)
            except httpx.TimeoutException:
                return None, "timeout", url
            except httpx.HTTPError:
                return None, "erro_conexao", url
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                resp.close()
                url = urljoin(url, resp.headers["location"])
                continue
            return resp, None, url
        return None, "redirecionamentos_demais", url

    def _robots_for(self, url: str) -> RobotFileParser | None:
        """None = tudo permitido. Devolve um parser vazio que nega tudo quando o robots.txt não pôde ser lido (5xx)."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        rp: RobotFileParser | None
        resp, err, _ = self._request(f"{origin}/robots.txt", self.delay)
        if resp is None:
            rp = self._deny_all()  # não deu para ler o robots.txt: por educação, não lê o site
        else:
            try:
                body = resp.read()[:500_000].decode("utf-8", "replace")
            finally:
                resp.close()
            if resp.status_code >= 500:
                rp = self._deny_all()
            elif resp.status_code >= 400:
                rp = None  # sem robots.txt: sem restrições
            else:
                rp = RobotFileParser()
                rp.parse(body.splitlines())
        self._robots[origin] = rp
        return rp

    @staticmethod
    def _deny_all() -> RobotFileParser:
        rp = RobotFileParser()
        rp.parse(["User-agent: *", "Disallow: /"])
        return rp

    def get(self, url: str) -> FetchResult:
        if self._check(url):  # endereço interno/inválido: nem chega a pedir o robots.txt
            return FetchResult(url, error="bloqueado_rede")
        rp = self._robots_for(url)
        agent = self.ua.split("/")[0]
        if rp is not None and not rp.can_fetch(agent, url):
            return FetchResult(url, error="bloqueado_robots")
        delay = max(self.delay, float(rp.crawl_delay(agent) or 0)) if rp is not None else self.delay
        resp, err, final = self._request(url, delay)
        if resp is None:
            return FetchResult(final, error=err)
        try:
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype and ctype not in TEXT_TYPES:
                return FetchResult(final, status=resp.status_code, error="tipo_nao_suportado")
            raw = b""
            for chunk in resp.iter_bytes():
                raw += chunk
                if len(raw) > MAX_BYTES:
                    raw = raw[:MAX_BYTES]
                    break
            text = raw.decode(resp.encoding or "utf-8", "replace")
            return FetchResult(final, status=resp.status_code,
                               error=None if resp.status_code < 400 else "erro_http", text=text)
        except httpx.HTTPError:
            return FetchResult(final, error="erro_conexao")
        finally:
            resp.close()
