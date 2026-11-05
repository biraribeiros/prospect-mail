"""Etapa 6b: monta o email com os cabeçalhos obrigatórios (List-Unsubscribe RFC 8058, Message-ID próprio, texto puro)
e o link de descadastro assinado."""
import uuid
from email.message import EmailMessage
from email.utils import formatdate

from itsdangerous import BadSignature, URLSafeSerializer

from app.core.config import get_settings

SALT = "descadastro"


def _serializer() -> URLSafeSerializer:
    s = get_settings()
    return URLSafeSerializer(s.unsubscribe_secret or s.secret_key, salt=SALT)


def make_unsub_token(ws: int, email: str) -> str:
    return _serializer().dumps({"w": ws, "e": email.lower()})


def read_unsub_token(token: str) -> tuple[int, str] | None:
    try:
        d = _serializer().loads(token)
        return int(d["w"]), str(d["e"])
    except (BadSignature, KeyError, ValueError, TypeError):
        return None


def new_message_id(domain: str) -> str:
    return f"<{uuid.uuid4().hex}@{domain}>"


FOOTER = "\n\n--\nSe não quiser mais receber mensagens como esta, é só acessar: {url}"


def build_email(*, from_addr: str, from_name: str | None, to_addr: str, subject: str, body: str, ws: int,
                message_id: str, in_reply_to: str | None = None, references: list[str] | None = None) -> EmailMessage:
    base = get_settings().public_base_url.rstrip("/")
    url = f"{base}/u/{make_unsub_token(ws, to_addr)}"
    m = EmailMessage()
    m["From"] = f"{from_name} <{from_addr}>" if from_name else from_addr
    m["To"] = to_addr
    m["Subject"] = subject
    m["Date"] = formatdate(localtime=False)
    m["Message-ID"] = message_id
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = " ".join((references or []) + [in_reply_to])
    m["List-Unsubscribe"] = f"<{url}>, <mailto:{from_addr}?subject=descadastrar>"
    m["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    m["Auto-Submitted"] = "no"
    m.set_content(body.rstrip() + FOOTER.format(url=url))
    return m
