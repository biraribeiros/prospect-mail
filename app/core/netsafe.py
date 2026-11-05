"""Proteção contra SSRF: só endereços públicos, só http/https nas portas 80/443.

Usada pelo crawler (sites vêm de fontes externas). Confere o nome de domínio resolvido a cada requisição.
Limite conhecido: entre a checagem e a conexão o DNS poderia mudar (DNS rebinding); a conexão final é feita pelo httpx.
"""
import ipaddress
import socket
from urllib.parse import urlsplit

ALLOWED_PORTS = {None, 80, 443}


def ip_is_public(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def check_url(url: str) -> str | None:
    """Devolve o motivo do bloqueio, ou None se o endereço é público e permitido."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "endereço inválido"
    if parts.scheme not in ("http", "https"):
        return "protocolo não permitido"
    host = (parts.hostname or "").strip().lower()
    if not host or host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        return "servidor não permitido"
    if port not in ALLOWED_PORTS:
        return "porta não permitida"
    if parts.username or parts.password:
        return "credenciais na URL não são permitidas"
    try:
        ipaddress.ip_address(host)
        addrs = [host]
    except ValueError:
        try:
            addrs = {info[4][0] for info in socket.getaddrinfo(host, port or (443 if parts.scheme == "https" else 80),
                                                              type=socket.SOCK_STREAM)}
        except (socket.gaierror, UnicodeError):
            return "domínio não encontrado"
    if not addrs or not all(ip_is_public(a) for a in addrs):
        return "endereço de rede interna"
    return None
