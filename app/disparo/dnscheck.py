"""Etapa 6a: pré-voo de DNS do domínio de envio — SPF, DKIM, DMARC e DNS reverso. Se algo falhar, o envio fica bloqueado."""
import re
from dataclasses import dataclass, field
from typing import Protocol

import dns.exception
import dns.resolver
import dns.reversename


class DnsLookup(Protocol):
    def txt(self, name: str) -> list[str]: ...
    def a(self, host: str) -> list[str]: ...
    def ptr(self, ip: str) -> list[str]: ...


class RealDns:
    def __init__(self, timeout: float = 5.0):
        self.r = dns.resolver.Resolver()
        self.r.lifetime = self.r.timeout = timeout

    def txt(self, name: str) -> list[str]:
        try:
            return ["".join(p.decode() for p in rr.strings) for rr in self.r.resolve(name, "TXT")]
        except dns.exception.DNSException:
            return []

    def a(self, host: str) -> list[str]:
        try:
            return [rr.address for rr in self.r.resolve(host, "A")]
        except dns.exception.DNSException:
            return []

    def ptr(self, ip: str) -> list[str]:
        try:
            return [str(rr).rstrip(".") for rr in self.r.resolve(dns.reversename.from_address(ip), "PTR")]
        except dns.exception.DNSException:
            return []


@dataclass
class DnsReport:
    spf_ok: bool = False
    dkim_ok: bool = False
    dmarc_ok: bool = False
    rdns_ok: bool = False
    problemas: list[str] = field(default_factory=list)
    detalhes: dict = field(default_factory=dict)

    @property
    def pronto(self) -> bool:
        return self.spf_ok and self.dkim_ok and self.dmarc_ok and self.rdns_ok


def check_spf(records: list[str]) -> tuple[bool, str]:
    spf = [r for r in records if r.lower().startswith("v=spf1")]
    if len(spf) != 1:
        return False, "SPF ausente" if not spf else "mais de um registro SPF (só pode haver um)"
    r = spf[0].lower()
    if re.search(r"(^|\s)\+?all\b", r) and not re.search(r"[-~]all", r):
        return False, "SPF com '+all' ou '?all' libera qualquer servidor"
    if not re.search(r"[-~]all\s*$", r):
        return False, "SPF precisa terminar em '~all' ou '-all'"
    if not re.search(r"include:|ip4:|ip6:|(^|\s)a(\s|:)|(^|\s)mx(\s|:)", r):
        return False, "SPF não autoriza nenhum servidor"
    return True, ""


def check_dkim(records: list[str]) -> tuple[bool, str]:
    for r in records:
        if "v=dkim1" in r.lower().replace(" ", "") or ("k=rsa" in r.lower() and "p=" in r.lower()):
            m = re.search(r"p=([A-Za-z0-9+/=]*)", r)
            if m and len(m.group(1)) > 20:
                return True, ""
            return False, "chave DKIM vazia (revogada)"
    return False, "DKIM ausente para o seletor informado"


def check_dmarc(records: list[str]) -> tuple[bool, str]:
    d = [r for r in records if r.lower().replace(" ", "").startswith("v=dmarc1")]
    if not d:
        return False, "DMARC ausente"
    m = re.search(r"\bp=(\w+)", d[0].replace(" ", ""), re.I)
    pol = m.group(1).lower() if m else ""
    if pol not in ("quarantine", "reject"):
        return False, f"DMARC com política '{pol or 'indefinida'}': use 'quarantine' ou 'reject' para prospecção"
    return True, ""


def check_rdns(dns_: DnsLookup, smtp_host: str | None) -> tuple[bool, str]:
    """DNS reverso confirmado: o IP do servidor de envio aponta para um nome que, por sua vez, aponta de volta para o IP."""
    if not smtp_host:
        return False, "servidor SMTP não informado para checar o DNS reverso"
    ips = dns_.a(smtp_host)
    if not ips:
        return False, f"não foi possível resolver {smtp_host}"
    for ip in ips:
        for name in dns_.ptr(ip):
            if ip in dns_.a(name):
                return True, ""
    return False, "DNS reverso ausente ou que não confere com o IP do servidor de envio"


def preflight(domain: str, selector: str | None, smtp_host: str | None, dns_: DnsLookup) -> DnsReport:
    rep = DnsReport()
    rep.spf_ok, msg = check_spf(dns_.txt(domain))
    msg and rep.problemas.append(msg)
    if selector:
        rep.dkim_ok, msg = check_dkim(dns_.txt(f"{selector}._domainkey.{domain}"))
    else:
        rep.dkim_ok, msg = False, "informe o seletor DKIM (ex.: default, google, selector1)"
    msg and rep.problemas.append(msg)
    rep.dmarc_ok, msg = check_dmarc(dns_.txt(f"_dmarc.{domain}"))
    msg and rep.problemas.append(msg)
    rep.rdns_ok, msg = check_rdns(dns_, smtp_host)
    msg and rep.problemas.append(msg)
    rep.detalhes = {"problemas": rep.problemas, "selector": selector, "smtp_host": smtp_host}
    return rep
