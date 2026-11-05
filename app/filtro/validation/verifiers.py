"""Etapa 4: interface EmailVerifier com 3 opções (Reacher, MillionVerifier, ZeroBounce).

Nenhum teste chama a rede de verdade: tudo recebe um httpx.Client injetável.
Aviso permanente: "SMTP não garante que a caixa existe" — por isso só o resultado 'valido' do provedor vira 'valido'.
"""
import socket
from dataclasses import dataclass, field
from typing import Protocol

import httpx

SMTP_NOTE = "A verificação SMTP não garante que a caixa existe: alguns servidores aceitam qualquer endereço."


@dataclass
class VerifierResult:
    status: str  # valido | arriscado | invalido | desconhecido
    catch_all: bool | None = None
    custo_usd: float = 0.0
    detalhe: str = ""
    raw: dict = field(default_factory=dict)


class VerifierError(Exception):
    """Falha do provedor (chave inválida, sem saldo, fora do ar). O contato fica para uma próxima tentativa."""


class EmailVerifier(Protocol):
    nome: str

    def verify(self, email: str) -> VerifierResult: ...


class ReacherVerifier:
    nome = "reacher"

    def __init__(self, base_url: str, http: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.http = http or httpx.Client(timeout=45)

    def verify(self, email: str) -> VerifierResult:
        try:
            r = self.http.post(f"{self.base_url}/v0/check_email", json={"to_email": email})
            r.raise_for_status()
            d = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise VerifierError(f"Reacher indisponível ({type(e).__name__})") from e
        reach = d.get("is_reachable", "unknown")
        smtp = d.get("smtp") or {}
        catch = smtp.get("is_catch_all")
        if d.get("misc", {}).get("is_disposable"):
            return VerifierResult("invalido", catch, 0.0, "descartável", {"is_reachable": reach})
        status = {"safe": "valido", "invalid": "invalido", "risky": "arriscado"}.get(reach, "desconhecido")
        if catch:
            status = "arriscado"
        return VerifierResult(status, catch, 0.0, f"reacher:{reach}", {"is_reachable": reach})


class MillionVerifier:
    nome = "millionverifier"
    URL = "https://api.millionverifier.com/api/v3/"

    def __init__(self, api_key: str, http: httpx.Client | None = None, price_usd: float = 0.0004):
        self.key, self.price = api_key, price_usd
        self.http = http or httpx.Client(timeout=30)

    def verify(self, email: str) -> VerifierResult:
        try:
            r = self.http.get(self.URL, params={"api": self.key, "email": email})
            r.raise_for_status()
            d = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise VerifierError(f"MillionVerifier indisponível ({type(e).__name__})") from e
        if d.get("error"):
            raise VerifierError(f"MillionVerifier: {str(d['error'])[:80]}")
        res = d.get("result", "unknown")
        status = {"ok": "valido", "invalid": "invalido", "disposable": "invalido", "catch_all": "arriscado",
                  "unknown": "desconhecido"}.get(res, "desconhecido")
        return VerifierResult(status, True if res == "catch_all" else None, self.price, f"millionverifier:{res}",
                              {"result": res})


class ZeroBounceVerifier:
    nome = "zerobounce"
    URL = "https://api.zerobounce.net/v2/validate"

    def __init__(self, api_key: str, http: httpx.Client | None = None, price_usd: float = 0.008):
        self.key, self.price = api_key, price_usd
        self.http = http or httpx.Client(timeout=30)

    def verify(self, email: str) -> VerifierResult:
        try:
            r = self.http.get(self.URL, params={"api_key": self.key, "email": email})
            r.raise_for_status()
            d = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise VerifierError(f"ZeroBounce indisponível ({type(e).__name__})") from e
        if d.get("error"):
            raise VerifierError(f"ZeroBounce: {str(d['error'])[:80]}")
        res = d.get("status", "unknown")
        status = {"valid": "valido", "invalid": "invalido", "catch-all": "arriscado", "unknown": "desconhecido",
                  "spamtrap": "invalido", "abuse": "invalido", "do_not_mail": "invalido"}.get(res, "desconhecido")
        return VerifierResult(status, True if res == "catch-all" else None, self.price, f"zerobounce:{res}", {"status": res})


def port25_open(host: str = "gmail-smtp-in.l.google.com", timeout: float = 5.0) -> bool:
    """Teste da porta 25 de saída. Em PCs e hospedagens compartilhadas costuma estar fechada."""
    try:
        with socket.create_connection((host, 25), timeout=timeout):
            return True
    except OSError:
        return False


def build_verifier(provider: str, api_key: str = "", reacher_url: str = "", http: httpx.Client | None = None,
                   port25: bool | None = None) -> tuple[EmailVerifier | None, str]:
    """Escolhe o verificador. Devolve (verificador | None, aviso para o painel).

    Reacher precisa de porta 25 aberta: se estiver fechada e houver chave de API paga, usa a API e avisa."""
    p = (provider or "nenhum").lower()
    if p == "reacher":
        if not reacher_url:
            return None, "Reacher escolhido, mas REACHER_URL não está configurada."
        if (port25 if port25 is not None else port25_open()):
            return ReacherVerifier(reacher_url, http), ""
        if api_key:
            return MillionVerifier(api_key, http), "Porta 25 fechada neste servidor: usando a API paga no lugar do Reacher."
        return None, "Porta 25 fechada: o Reacher não funciona aqui. Configure MillionVerifier ou ZeroBounce."
    if p in ("millionverifier", "zerobounce"):
        if not api_key:
            return None, f"Falta VERIFIER_API_KEY para o {p}."
        return (MillionVerifier(api_key, http) if p == "millionverifier" else ZeroBounceVerifier(api_key, http)), ""
    return None, "Nenhum verificador configurado: os emails só passam pelas checagens locais (sintaxe, MX) e ficam 'arriscado'."
