import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings
from app.core.security import decrypt_json, encrypt_json, hash_password, verify_password


@pytest.mark.parametrize("valor,esperado", [
    ("false", False), ("FALSE", False), (" False ", False),
    ("true", True), ("0", True), ("no", True), ("", True), ("off", True),
])
def test_dry_run_so_desliga_com_false_exato(valor, esperado):
    assert Settings(_env_file=None, DRY_RUN=valor).dry_run is esperado


def test_dry_run_padrao_e_true(monkeypatch):
    monkeypatch.delenv("DRY_RUN", raising=False)
    assert Settings(_env_file=None).dry_run is True


def test_producao_recusa_secret_padrao():
    from app.core.config import INSECURE_SECRET

    with pytest.raises(RuntimeError):
        Settings(_env_file=None, APP_ENV="prod", SECRET_KEY=INSECURE_SECRET).check_production_safety()
    Settings(_env_file=None, APP_ENV="prod", SECRET_KEY="uma-chave-real-e-longa").check_production_safety()
    Settings(_env_file=None, APP_ENV="dev", SECRET_KEY=INSECURE_SECRET).check_production_safety()


def test_senha_argon2():
    h = hash_password("segredo")
    assert h.startswith("$argon2")
    assert verify_password("segredo", h)
    assert not verify_password("outra", h)
    assert not verify_password("segredo", None)


def test_fernet_roundtrip_e_rotacao():
    k_old, k_new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    token = encrypt_json({"t": "abc"}, keys=k_old)
    assert decrypt_json(token, keys=f"{k_new},{k_old}") == {"t": "abc"}  # chave antiga ainda decifra
    novo = encrypt_json({"t": "abc"}, keys=f"{k_new},{k_old}")  # passa a cifrar com a nova
    assert decrypt_json(novo, keys=k_new) == {"t": "abc"}
    with pytest.raises(InvalidToken):
        decrypt_json(novo, keys=k_old)


def test_fernet_sem_chave_falha():
    with pytest.raises(RuntimeError):
        encrypt_json({"a": 1}, keys=" , ")
