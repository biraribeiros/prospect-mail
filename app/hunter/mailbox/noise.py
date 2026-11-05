"""Filtro de ruído: tira robôs, notificações, bancos, redes sociais e marketplaces. Editável por workspace."""
import re
from dataclasses import dataclass, field

from app.hunter.mailbox.base import RawMessage

LOCAL_PATTERNS = [
    r"no[-_.]?reply", r"do[-_.]?not[-_.]?reply", r"nao[-_.]?responda", r"no[-_.]?answer",
    r"mailer[-_.]?daemon", r"postmaster", r"daemon", r"bounces?", r"mail[-_.]?delivery.*",
    r"notifications?", r"notificacao", r"notificacoes", r"newsletter.*", r"alertas?", r"automatico", r"system",
]
DOMINIOS_RUIDO = [
    # bancos e pagamentos
    "itau.com.br", "bradesco.com.br", "santander.com.br", "bb.com.br", "caixa.gov.br", "nubank.com.br",
    "inter.co", "sicredi.com.br", "sicoob.com.br", "c6bank.com.br", "mercadopago.com", "paypal.com",
    "pagseguro.com.br", "stripe.com",
    # redes sociais
    "facebookmail.com", "facebook.com", "linkedin.com", "instagram.com", "twitter.com", "x.com",
    "tiktok.com", "youtube.com", "pinterest.com",
    # marketplaces e varejo
    "mercadolivre.com.br", "mercadolivre.com", "amazon.com.br", "amazon.com", "shopee.com.br",
    "magazineluiza.com.br", "americanas.com.br", "aliexpress.com", "olx.com.br",
    # plataformas e notificações de sistemas
    "google.com", "accounts.google.com", "microsoft.com", "microsoftonline.com", "github.com",
    "notion.so", "slack.com", "zoom.us", "dropbox.com", "canva.com", "apple.com",
]
DOMINIOS_PESSOAIS = {
    "gmail.com", "hotmail.com", "outlook.com", "live.com", "msn.com", "yahoo.com", "yahoo.com.br",
    "icloud.com", "bol.com.br", "uol.com.br", "terra.com.br", "ig.com.br", "globo.com", "r7.com",
    "protonmail.com", "proton.me",
}
_BULK_PRECEDENCE = ("bulk", "list", "junk")


def domain_of(addr: str) -> str:
    return addr.rsplit("@", 1)[-1].lower()


def dominio_tipo(addr: str) -> str:
    return "pessoal" if domain_of(addr) in DOMINIOS_PESSOAIS else "corporativo"


def _in_domains(domain: str, domains: set[str]) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in domains)


@dataclass
class NoiseFilter:
    locais: list[str] = field(default_factory=list)  # extras do workspace (texto simples, sem regex)
    dominios: set[str] = field(default_factory=set)
    permitidos: set[str] = field(default_factory=set)  # emails/domínios que NUNCA são ruído

    @classmethod
    def from_config(cls, config: dict | None) -> "NoiseFilter":
        c = (config or {}).get("ruido", {}) or {}
        return cls(
            locais=[x.strip().lower() for x in c.get("locais", []) if x.strip()],
            dominios={x.strip().lower() for x in c.get("dominios", []) if x.strip()},
            permitidos={x.strip().lower() for x in c.get("permitidos", []) if x.strip()},
        )

    def _allowed(self, addr: str) -> bool:
        return addr in self.permitidos or _in_domains(domain_of(addr), self.permitidos)

    def is_noise_address(self, addr: str) -> bool:
        addr = addr.lower()
        if self._allowed(addr):
            return False
        local, domain = addr.rsplit("@", 1)
        local = local.split("+", 1)[0]
        if any(re.fullmatch(p, local) for p in LOCAL_PATTERNS) or local in self.locais:
            return True
        return _in_domains(domain, set(DOMINIOS_RUIDO) | self.dominios)

    def is_noise_message(self, msg: RawMessage) -> bool:
        """Mensagem recebida de robô/lista: pelo endereço ou pelos cabeçalhos de envio em massa."""
        if self._allowed(msg.from_addr):
            return False
        if self.is_noise_address(msg.from_addr):
            return True
        h = msg.headers
        if "list-unsubscribe" in h or "list-id" in h:
            return True
        if any(b in h.get("precedence", "") for b in _BULK_PRECEDENCE):
            return True
        auto = h.get("auto-submitted", "")
        return bool(auto) and auto != "no"
