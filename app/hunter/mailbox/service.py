"""Varredura de uma caixa: lê mensagens, filtra ruído, cria/atualiza contatos, grava evidência e conformidade.

Garantias:
- Idempotente: reprocessar a mesma caixa/arquivo não cria nada novo (job_run + chave única por mensagem/contato).
- Só metadados: o corpo do email e o assunto nunca vão para o banco; só o trecho curto da assinatura.
- Toda coleta tem evidência (fonte, URL, confiança) e registro de conformidade.
"""
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.compliance import create_compliance_record
from app.core.config import get_settings
from app.core.jobs import finish_job, start_job
from app.core.models import Company, Contact, MailboxAccount, MailInteraction
from app.core.provenance import record_evidence
from app.hunter.mailbox.base import MailboxReader, RawMessage
from app.hunter.mailbox.llm import ClaudeSignatureExtractor
from app.hunter.mailbox.noise import NoiseFilter, domain_of, dominio_tipo
from app.hunter.mailbox.score import detect_keywords, relationship_score
from app.hunter.mailbox.signature import SignatureInfo, parse_signature

ORIGEM = "caixa_email"
CONF_EMAIL, CONF_NOME, CONF_ASSINATURA = 0.9, 0.6, 0.6
OVERLAP_DIAS = 1


@dataclass
class ScanResult:
    job_id: int | None = None
    repetido: bool = False
    lidas: int = 0
    ruido: int = 0
    interacoes_novas: int = 0
    contatos_novos: int = 0
    contatos_atualizados: int = 0
    assinaturas_ia: int = 0
    custo_usd: float = 0.0


def file_sha(path: str | Path) -> str:
    p, h = Path(path), hashlib.sha256()
    if p.is_dir():
        for f in sorted(p.glob("*.eml")):
            h.update(f.name.encode() + str(f.stat().st_size).encode())
    else:
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


def job_params(mailbox: MailboxAccount, since: datetime | None, content_sha: str | None) -> dict:
    p: dict = {"mailbox_id": mailbox.id, "since": since.isoformat() if since else None}
    if content_sha:  # arquivo: mesmo conteúdo = mesmo job (não reprocessa)
        p["conteudo"] = content_sha
    else:  # caixa ao vivo: cada varredura concluída muda o ponto de partida
        p["ultima"] = mailbox.ultima_varredura.isoformat() if mailbox.ultima_varredura else None
    return p


def _clean_name(name: str | None, addr: str) -> str | None:
    if not name or "@" in name:
        return None
    name = " ".join(name.replace('"', "").split())
    return name if len(name) >= 2 and name.lower() != addr.split("@")[0] else None


def _message_key(msg: RawMessage, participants: list[str]) -> str:
    if msg.message_id:
        return msg.message_id[:300]
    raw = f"{msg.date}|{msg.from_addr}|{','.join(sorted(participants))}|{msg.subject}"
    return "sha1:" + hashlib.sha1(raw.encode()).hexdigest()


def scan_mailbox(session: Session, mailbox: MailboxAccount, reader: MailboxReader, *, own_addresses: set[str],
                 noise: NoiseFilter, llm: ClaudeSignatureExtractor | None = None, since: datetime | None = None,
                 content_sha: str | None = None) -> ScanResult:
    ws = mailbox.workspace_id
    res = ScanResult()
    res.job_id, created = start_job(session, "scan_caixa", job_params(mailbox, since, content_sha), ws)
    if not created:
        res.repetido = True
        return res

    settings = get_settings()
    companies: dict[str, int | None] = {}
    touched: set[int] = set()
    new_contacts: set[int] = set()
    llm_calls = 0
    source = f"caixa_email:{mailbox.tipo}"
    url = f"mailbox://{mailbox.id}"

    def company_for(domain: str) -> int | None:
        if domain not in companies:
            companies[domain] = session.scalar(
                select(Company.id).where(Company.workspace_id == ws, Company.dominio == domain))
        return companies[domain]

    def upsert(addr: str, name: str | None) -> tuple[Contact, bool]:
        contact = session.scalar(select(Contact).where(Contact.workspace_id == ws, Contact.email == addr))
        if contact is None:
            nome = _clean_name(name, addr)
            contact = Contact(
                workspace_id=ws, email=addr, nome_completo=nome,
                primeiro_nome=nome.split()[0].capitalize() if nome else None,
                origem=ORIGEM, dominio_tipo=dominio_tipo(addr), company_id=company_for(domain_of(addr)))
            session.add(contact)
            session.flush()
            create_compliance_record(session, workspace_id=ws, entity_type="contact", entity_id=contact.id,
                                     origem=ORIGEM)
            record_evidence(session, workspace_id=ws, entity_type="contact", entity_id=contact.id, field="email",
                            value=addr, source=source, url=url, confidence=CONF_EMAIL)
            if contact.nome_completo:
                record_evidence(session, workspace_id=ws, entity_type="contact", entity_id=contact.id,
                                field="nome_completo", value=contact.nome_completo, source=source, url=url,
                                confidence=CONF_NOME)
            return contact, True
        return contact, False

    def apply_signature(contact: Contact, info: SignatureInfo) -> None:
        for campo, valor in (("nome_completo", info.nome), ("cargo", info.cargo),
                             ("telefone", info.telefone), ("site", info.site)):
            if not valor:
                continue
            record_evidence(session, workspace_id=ws, entity_type="contact", entity_id=contact.id, field=campo,
                            value=valor, source=f"{source}:assinatura", url=url, confidence=CONF_ASSINATURA)
            if getattr(contact, campo) is None:
                setattr(contact, campo, valor)
                if campo == "nome_completo":
                    contact.primeiro_nome = valor.split()[0].capitalize()
        if info.trecho and contact.assinatura_trecho is None:
            contact.assinatura_trecho = info.trecho

    for msg in reader.scan(since):
        res.lidas += 1
        direcao = msg.direction_hint or ("enviado" if msg.from_addr in own_addresses else "recebido")
        if direcao == "recebido":
            if msg.from_addr in own_addresses:
                continue
            if noise.is_noise_message(msg):
                res.ruido += 1
                continue
            people = [(msg.from_name, msg.from_addr)]
        else:
            candidates = [p for p in msg.to + msg.cc if p[1] not in own_addresses]
            people = [p for p in candidates if not noise.is_noise_address(p[1])]
            if candidates and not people:
                res.ruido += 1
        if not people:
            continue

        key = _message_key(msg, [a for _, a in people])
        keywords = detect_keywords(msg.subject, msg.body_text)
        signature: SignatureInfo | None = None
        for name, addr in people:
            contact, is_new = upsert(addr, name)
            if is_new:
                res.contatos_novos += 1
                new_contacts.add(contact.id)
            already = session.scalar(select(MailInteraction.id).where(
                MailInteraction.mailbox_id == mailbox.id, MailInteraction.message_key == key,
                MailInteraction.contact_id == contact.id))
            if already:
                continue
            session.add(MailInteraction(workspace_id=ws, mailbox_id=mailbox.id, contact_id=contact.id,
                                        message_key=key, direcao=direcao, ocorrido_em=msg.date,
                                        palavras_chave=keywords))
            res.interacoes_novas += 1
            touched.add(contact.id)
            if direcao == "recebido":
                if signature is None:
                    signature = parse_signature(msg.body_text)
                    if (not signature.nome and not signature.cargo and signature.trecho and llm
                            and llm_calls < settings.llm_max_calls_per_scan):
                        llm_calls += 1
                        try:
                            ia, custo = llm.extract(signature.trecho)
                        except Exception:  # noqa: BLE001 - a IA é reserva; falha dela não derruba a varredura
                            ia, custo = None, 0.0
                        res.custo_usd += custo
                        if ia:
                            res.assinaturas_ia += 1
                            ia.trecho = signature.trecho
                            ia.telefone = ia.telefone or signature.telefone
                            ia.site = ia.site or signature.site
                            signature = ia
                apply_signature(contact, signature)
    session.flush()
    _recompute(session, touched)
    res.contatos_atualizados = len(touched - new_contacts)

    cursor = reader.cursor()
    if cursor:
        mailbox.cursor = {**(mailbox.cursor or {}), **cursor}
    if not content_sha:
        mailbox.ultima_varredura = datetime.now(timezone.utc)
    finish_job(session, res.job_id, {
        "lidas": res.lidas, "ruido": res.ruido, "interacoes_novas": res.interacoes_novas,
        "contatos_novos": res.contatos_novos, "contatos_atualizados": res.contatos_atualizados,
        "assinaturas_ia": res.assinaturas_ia}, custo_usd=res.custo_usd)
    return res


def _recompute(session: Session, contact_ids: set[int]) -> None:
    """Recalcula contagens, última interação e nota dos contatos tocados (a partir dos metadados gravados)."""
    ids = sorted(contact_ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        agg: dict[int, dict] = {}
        for cid, direcao, n, last in session.execute(
            select(MailInteraction.contact_id, MailInteraction.direcao, func.count(), func.max(MailInteraction.ocorrido_em))
            .where(MailInteraction.contact_id.in_(chunk)).group_by(MailInteraction.contact_id, MailInteraction.direcao)
        ):
            d = agg.setdefault(cid, {"enviado": 0, "recebido": 0, "last": None, "kw": set()})
            d[direcao] = n
            if last and (d["last"] is None or last > d["last"]):
                d["last"] = last
        for cid, kws in session.execute(
            select(MailInteraction.contact_id, MailInteraction.palavras_chave)
            .where(MailInteraction.contact_id.in_(chunk), func.cardinality(MailInteraction.palavras_chave) > 0)
        ):
            agg.setdefault(cid, {"enviado": 0, "recebido": 0, "last": None, "kw": set()})["kw"].update(kws)
        for contact in session.scalars(select(Contact).where(Contact.id.in_(chunk))):
            d = agg.get(contact.id)
            if not d:
                continue
            contact.qtd_enviados, contact.qtd_recebidos = d["enviado"], d["recebido"]
            contact.ultima_interacao = d["last"]
            contact.relationship_score = relationship_score(d["enviado"], d["recebido"], d["last"], d["kw"])
    session.flush()
