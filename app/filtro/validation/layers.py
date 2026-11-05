"""Camadas locais da validação (sem custo): sintaxe → descartável → conta de função → MX."""
from dataclasses import dataclass
from typing import Protocol

import dns.exception
import dns.resolver
from email_validator import EmailNotValidError, validate_email

DESCARTAVEIS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com", "temp-mail.org", "yopmail.com",
    "trashmail.com", "throwawaymail.com", "getnada.com", "sharklasers.com", "dispostable.com", "maildrop.cc",
    "fakeinbox.com", "mintemail.com", "mohmal.com", "emailondeck.com", "spamgourmet.com", "tempinbox.com",
    "mailnesia.com", "mytemp.email", "tempail.com", "burnermail.io", "discard.email", "moakt.com",
}
# conta de função: caixa compartilhada, raramente lida por quem decide
CONTAS_FUNCAO = {"abuse", "postmaster", "hostmaster", "webmaster", "noreply", "no-reply", "donotreply", "do-not-reply",
                 "mailer-daemon", "root", "admin", "administrator", "spam", "security", "privacy", "newsletter"}


@dataclass
class LayerFail:
    camada: str
    status: str  # invalido | bloqueado | arriscado
    motivo: str


class Resolver(Protocol):
    def mx(self, domain: str) -> list[str] | None:
        """Lista de servidores de email; [] = domínio existe mas sem MX/A; None = domínio não existe."""


class DnsResolver:
    def __init__(self, timeout: float = 5.0):
        self.r = dns.resolver.Resolver()
        self.r.lifetime = self.r.timeout = timeout
        self._cache: dict[str, list[str] | None] = {}

    def mx(self, domain: str) -> list[str] | None:
        if domain in self._cache:
            return self._cache[domain]
        try:
            hosts = sorted(str(a.exchange).rstrip(".") for a in self.r.resolve(domain, "MX"))
            res: list[str] | None = [h for h in hosts if h]  # "MX nulo" (RFC 7505) vira lista vazia
        except dns.resolver.NXDOMAIN:
            res = None
        except dns.resolver.NoAnswer:
            try:  # sem MX: o padrão da internet é usar o endereço do domínio (registro A)
                self.r.resolve(domain, "A")
                res = [domain]
            except (dns.exception.DNSException):
                res = []
        except dns.exception.Timeout:
            raise
        except dns.exception.DNSException:
            res = []
        self._cache[domain] = res
        return res


def check_syntax(email: str) -> tuple[str | None, LayerFail | None]:
    try:
        v = validate_email(email, check_deliverability=False, allow_smtputf8=False)
    except EmailNotValidError as e:
        return None, LayerFail("sintaxe", "invalido", str(e)[:120])
    return v.normalized.lower(), None


def check_disposable(email: str) -> LayerFail | None:
    dom = email.rsplit("@", 1)[1]
    if dom in DESCARTAVEIS or any(dom.endswith("." + d) for d in DESCARTAVEIS):
        return LayerFail("descartavel", "bloqueado", "domínio de email descartável")
    return None


def check_role(email: str) -> LayerFail | None:
    local = email.split("@")[0].lower()
    if local in CONTAS_FUNCAO:
        return LayerFail("conta_funcao", "arriscado", f"conta de função ({local}@): caixa compartilhada ou automática")
    return None


def check_mx(email: str, resolver: Resolver) -> LayerFail | None:
    dom = email.rsplit("@", 1)[1]
    try:
        hosts = resolver.mx(dom)
    except dns.exception.Timeout:
        return LayerFail("mx", "arriscado", "não foi possível consultar o DNS agora (tempo esgotado)")
    if hosts is None:
        return LayerFail("mx", "invalido", "o domínio do email não existe")
    if not hosts:
        return LayerFail("mx", "invalido", "o domínio não tem servidor de email")
    return None
