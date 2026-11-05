"""Gera caixas .mbox de teste (sem chamar nada externo)."""
import mailbox
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

OWN = "eu@minhaempresa.com.br"
SEGREDO_CORPO = "SEGREDO-CORPO-98765"
SEGREDO_ASSUNTO = "ASSUNTO-SECRETO-4321"


def assinatura(n: int) -> str:
    return (f"Atenciosamente,\nPessoa Numero{n}\nGerente de Marketing\n(34) 99999-{n:04d}\n"
            f"www.empresa{n % 40}.com.br")


def make_msg(*, mid: str, de: str, para: str, date: datetime, assunto: str, corpo: str,
             nome_de: str | None = None, extra: dict | None = None) -> EmailMessage:
    m = EmailMessage()
    m["Message-ID"] = f"<{mid}>"
    m["From"] = f"{nome_de} <{de}>" if nome_de else de
    m["To"] = para
    m["Date"] = format_datetime(date)
    m["Subject"] = assunto
    for k, v in (extra or {}).items():
        m[k] = v
    m.set_content(corpo)
    return m


def write_mbox(path: Path, msgs: list[EmailMessage]) -> Path:
    box = mailbox.mbox(str(path))
    box.lock()
    try:
        for m in msgs:
            box.add(m)
        box.flush()
    finally:
        box.unlock()
        box.close()
    return path


def build_1000(path: Path, now: datetime | None = None) -> dict:
    """1.000 mensagens: 700 recebidas de 300 pessoas, 200 enviadas, 100 de ruído. Devolve o gabarito."""
    now = now or datetime.now(timezone.utc)
    msgs, externos, ruido = [], set(), 0
    for i in range(1000):
        quando = now - timedelta(days=i % 90, hours=i % 24)
        n = i % 300
        pessoa = f"pessoa{n}@empresa{n % 40}.com.br"
        if i % 10 == 9:  # ruído (100)
            ruido += 1
            tipo = i % 30
            if tipo == 9:
                de, extra = "noreply@lojaqualquer.com.br", None
            elif tipo == 19:
                de, extra = "novidades@loja.com.br", {"List-Unsubscribe": "<mailto:sair@loja.com.br>"}
            else:
                de, extra = "alertas@itau.com.br", None
            msgs.append(make_msg(mid=f"m{i}@teste", de=de, para=OWN, date=quando, assunto="Promoção",
                                 corpo="Oferta imperdível", extra=extra))
        elif i % 5 == 4:  # enviadas (≈ 180)
            externos.add(pessoa)
            msgs.append(make_msg(mid=f"m{i}@teste", de=OWN, para=f"Pessoa Numero{n} <{pessoa}>", date=quando,
                                 assunto=f"Proposta {SEGREDO_ASSUNTO}" if i % 50 == 4 else "Conversa",
                                 corpo=f"Segue o orçamento. {SEGREDO_CORPO}\n\nAbraço,\nEu\nDiretor\n(34) 98888-0000"))
        else:  # recebidas (≈ 720)
            externos.add(pessoa)
            msgs.append(make_msg(mid=f"m{i}@teste", de=pessoa, nome_de=f"Pessoa Numero{n}", para=OWN, date=quando,
                                 assunto="Reunião sobre contrato" if i % 7 == 0 else "Oi",
                                 corpo=f"Olá, tudo bem? {SEGREDO_CORPO}\n\n{assinatura(n)}"))
    write_mbox(path, msgs)
    return {"externos": externos, "ruido": ruido, "total": 1000}
