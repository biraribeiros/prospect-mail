from datetime import datetime, timezone

from app.core.models import Company, Contact, MailboxAccount, OutboundMessage, SenderDomain
from app.core.security import encrypt_json

NOW = datetime(2026, 11, 9, 13, 0, tzinfo=timezone.utc)  # segunda-feira, 10h em São Paulo


class FakeSender:
    def __init__(self, falhar=()):
        self.enviadas, self.falhar = [], set(falhar)

    def send(self, mailbox, cfg, msg):
        if msg["To"] in self.falhar:
            raise ConnectionError("smtp fora do ar")
        self.enviadas.append((mailbox.id, msg))


class FakeDns:
    def __init__(self, txt=None, a=None, ptr=None):
        self._txt, self._a, self._ptr = txt or {}, a or {}, ptr or {}

    def txt(self, name):
        return self._txt.get(name, [])

    def a(self, host):
        return self._a.get(host, [])

    def ptr(self, ip):
        return self._ptr.get(ip, [])


def dns_ok(domain="envio-agencia.com.br", selector="default", host="mail.envio-agencia.com.br"):
    return FakeDns(
        txt={domain: ["v=spf1 include:_spf.hospedagem.com ~all"],
             f"{selector}._domainkey.{domain}": ["v=DKIM1; k=rsa; p=" + "A" * 80],
             f"_dmarc.{domain}": ["v=DMARC1; p=quarantine; rua=mailto:d@x.com"]},
        a={host: ["203.0.113.10"], "ptr.hospedagem.com": ["203.0.113.10"]},
        ptr={"203.0.113.10": ["ptr.hospedagem.com"]})


def hunter_box(db, ws, dominio="agenciavia.com.br"):
    mb = MailboxAccount(workspace_id=ws.id, tipo="imap", nome="Caixa lida", uso="hunter", dominio=dominio,
                        tokens_enc=encrypt_json({"host": "imap.x.com", "user": f"bira@{dominio}", "password": "x"}))
    db.add(mb)
    db.commit()
    return mb


def sender_box(db, ws, dominio="envio-agencia.com.br", pronto=True, nome=None, limite=0, tipo_dom="secundario"):
    d = SenderDomain(workspace_id=ws.id, nome=dominio, tipo=tipo_dom, spf_ok=pronto, dkim_ok=pronto, dmarc_ok=pronto, rdns_ok=pronto,
                     dkim_selector="default")
    db.add(d)
    db.flush()
    mb = MailboxAccount(workspace_id=ws.id, tipo="smtp", nome=nome or f"Envio {dominio}", uso="envio", dominio=dominio,
                        sender_domain_id=d.id, limite_diario=limite,
                        tokens_enc=encrypt_json({"host": "mail.x.com", "port": 587, "user": f"contato@{dominio}", "password": "x",
                                                 "from": f"contato@{dominio}", "from_name": "Equipe"}))
    db.add(mb)
    db.commit()
    return mb, d


def contato(db, ws, email, uf="SP", status="valido", empresa=True, **kw):
    c = None
    if empresa:
        c = Company(workspace_id=ws.id, razao_social=f"EMPRESA {email}", uf=uf)
        db.add(c)
        db.flush()
    ct = Contact(workspace_id=ws.id, email=email, email_status=status, company_id=c.id if c else None,
                 primeiro_nome=kw.pop("primeiro_nome", "Ana"), **kw)
    db.add(ct)
    db.commit()
    return ct, c


def mensagem(db, ws, ct, c=None, status="aprovada", **kw):
    m = OutboundMessage(workspace_id=ws.id, contact_id=ct.id, company_id=c.id if c else ct.company_id,
                        assunto=kw.pop("assunto", "Uma ideia"), corpo=kw.pop("corpo", "Olá, Ana. Vimos que vocês estão contratando."),
                        status=status, **kw)
    db.add(m)
    db.commit()
    return m
