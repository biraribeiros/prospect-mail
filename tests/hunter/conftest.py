import pytest

from app.core.models import MailboxAccount
from app.core.security import encrypt_json
from app.hunter.mailbox.noise import NoiseFilter
from tests.hunter.helpers import OWN


@pytest.fixture
def mailbox(db, world):
    """Caixa .mbox do workspace A (o caminho é preenchido pelo teste)."""
    def make(path: str, ws=None, nome="Caixa teste", hint=None):
        mb = MailboxAccount(workspace_id=(ws or world.a).id, tipo="mbox", nome=nome, dominio="minhaempresa.com.br",
                            tokens_enc=encrypt_json({"path": str(path), "emails": [OWN], "pasta_hint": hint}))
        db.add(mb)
        db.commit()
        return mb
    return make


@pytest.fixture
def noise():
    return NoiseFilter.from_config({})
