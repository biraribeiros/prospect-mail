import json
import secrets
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, MultiFernet

from app.core.config import get_settings

_hasher = PasswordHasher()
# Hash falso para igualar o tempo de resposta quando o email não existe.
_DUMMY_HASH = _hasher.hash("senha-inexistente")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def get_fernet(keys: str | None = None) -> MultiFernet:
    """MultiFernet: a primeira chave cifra; todas decifram (permite rotação sem perder dados)."""
    raw = keys if keys is not None else get_settings().fernet_keys
    parts = [k.strip() for k in raw.split(",") if k.strip()]
    if not parts:
        raise RuntimeError("FERNET_KEYS vazio. Gere uma chave (veja .env.example).")
    return MultiFernet([Fernet(k.encode()) for k in parts])


def encrypt_json(data: Any, keys: str | None = None) -> str:
    return get_fernet(keys).encrypt(json.dumps(data).encode()).decode()


def decrypt_json(token: str, keys: str | None = None) -> Any:
    return json.loads(get_fernet(keys).decrypt(token.encode()))


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_matches(expected: str | None, provided: str | None) -> bool:
    return bool(expected) and bool(provided) and secrets.compare_digest(expected, provided)
