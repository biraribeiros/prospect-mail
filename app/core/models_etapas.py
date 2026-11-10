"""Tabelas das Etapas 2b a 9. Importado no fim de app/core/models.py (use sempre `from app.core.models import ...`)."""
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import EMPTY_ARRAY, EMPTY_OBJ, TS, Base, TextArray

EMPTY_LIST = text("'[]'::jsonb")


# ---------------- Etapa 2b: sinais comerciais ----------------
class OpportunitySignal(Base):
    """Momento de compra detectado. Sem fonte verificável (url) não é gravado."""

    __tablename__ = "opportunity_signal"
    __table_args__ = (UniqueConstraint("workspace_id", "company_id", "tipo", "hash"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"), index=True)
    tipo: Mapped[str] = mapped_column(Text)  # vaga | expansao | inauguracao | lancamento | diretoria | feira | maps_ativo | maps_queda
    titulo: Mapped[str] = mapped_column(Text)
    fonte: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    confianca: Mapped[float] = mapped_column(Numeric(4, 3))
    polaridade: Mapped[str] = mapped_column(Text, default="positivo", server_default="positivo")
    hash: Mapped[str] = mapped_column(Text)
    detectado_em: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    valido_ate: Mapped[datetime | None] = mapped_column(TS)


# ---------------- Etapa 3: mesclagem reversível ----------------
class CompanyMerge(Base):
    __tablename__ = "company_merge"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    survivor_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"), index=True)
    merged_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"), index=True)
    motivo: Mapped[str] = mapped_column(Text)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_OBJ)
    criado_em: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    desfeito_em: Mapped[datetime | None] = mapped_column(TS)


# ---------------- Etapa 5b: inteligência comercial ----------------
class Offer(Base):
    __tablename__ = "offer"
    __table_args__ = (UniqueConstraint("workspace_id", "nome"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(Text)
    descricao: Mapped[str] = mapped_column(Text, default="")
    faixa_preco: Mapped[str | None] = mapped_column(Text)
    cases: Mapped[list[Any]] = mapped_column(JSONB, server_default=EMPTY_LIST)
    tags: Mapped[list[str]] = mapped_column(TextArray, server_default=EMPTY_ARRAY)  # liga a "ofertas_relacionadas" das necessidades
    tipos_sinal: Mapped[list[str]] = mapped_column(TextArray, server_default=EMPTY_ARRAY)
    departamentos: Mapped[list[str]] = mapped_column(TextArray, server_default=EMPTY_ARRAY)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class Hypothesis(Base):
    __tablename__ = "hypothesis"
    __table_args__ = (
        UniqueConstraint("contact_id", "offer_id", "signal_id"),
        CheckConstraint("status in ('pendente','aprovada','rejeitada')", name="ck_hypothesis_status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"), index=True)
    offer_id: Mapped[int] = mapped_column(ForeignKey("offer.id", ondelete="CASCADE"))
    signal_id: Mapped[int] = mapped_column(ForeignKey("opportunity_signal.id", ondelete="CASCADE"))
    texto: Mapped[str] = mapped_column(Text)
    justificativa: Mapped[str] = mapped_column(Text)
    nota: Mapped[int] = mapped_column(Integer)
    evidencias: Mapped[list[Any]] = mapped_column(JSONB, server_default=EMPTY_LIST)
    status: Mapped[str] = mapped_column(Text, default="pendente", server_default="pendente")
    decidido_por: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    decidido_em: Mapped[datetime | None] = mapped_column(TS)
    criado_em: Mapped[datetime] = mapped_column(TS, server_default=func.now())


# ---------------- Etapa 6a: domínios de envio ----------------
class SenderDomain(Base):
    __tablename__ = "sender_domain"
    __table_args__ = (
        UniqueConstraint("workspace_id", "nome"),
        CheckConstraint("tipo in ('principal','secundario')", name="ck_sender_domain_tipo"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(Text)
    tipo: Mapped[str] = mapped_column(Text, default="secundario", server_default="secundario")
    spf_ok: Mapped[bool | None] = mapped_column(Boolean)
    dkim_ok: Mapped[bool | None] = mapped_column(Boolean)
    dmarc_ok: Mapped[bool | None] = mapped_column(Boolean)
    rdns_ok: Mapped[bool | None] = mapped_column(Boolean)
    dkim_selector: Mapped[str | None] = mapped_column(Text)
    detalhes: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_OBJ)
    checado_em: Mapped[datetime | None] = mapped_column(TS)
    ja_enviou: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    criado_em: Mapped[datetime] = mapped_column(TS, server_default=func.now())


# ---------------- Etapas 6b, 7, 8: mensagens ----------------
class MessageTemplate(Base):
    __tablename__ = "message_template"
    __table_args__ = (
        UniqueConstraint("workspace_id", "nome"),
        CheckConstraint("objetivo in ('sonda','seguimento','ultimo_toque')", name="ck_template_objetivo"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(Text)
    objetivo: Mapped[str] = mapped_column(Text, default="sonda")
    departamento: Mapped[str | None] = mapped_column(Text)
    assunto: Mapped[str] = mapped_column(Text)
    corpo: Mapped[str] = mapped_column(Text)


class OutboundMessage(Base):
    __tablename__ = "outbound_message"
    __table_args__ = (
        CheckConstraint(
            "status in ('rascunho','aprovada','agendada','enviada','falhou','bloqueada','descartada','cancelada')",
            name="ck_outbound_status"),
        Index("ix_outbound_status_sched", "workspace_id", "status", "agendada_para"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("company.id", ondelete="SET NULL"))
    mailbox_id: Mapped[int | None] = mapped_column(ForeignKey("mailbox_account.id", ondelete="SET NULL"))
    hypothesis_id: Mapped[int | None] = mapped_column(ForeignKey("hypothesis.id", ondelete="SET NULL"))
    icp_id: Mapped[int | None] = mapped_column(ForeignKey("icp_profile.id", ondelete="SET NULL"), index=True)
    thread_raiz_id: Mapped[int | None] = mapped_column(Integer, index=True)  # id da 1ª mensagem da conversa
    passo: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    objetivo: Mapped[str] = mapped_column(Text, default="sonda", server_default="sonda")
    variante: Mapped[str | None] = mapped_column(Text)
    assunto: Mapped[str] = mapped_column(Text)
    corpo: Mapped[str] = mapped_column(Text)
    variacoes: Mapped[list[Any]] = mapped_column(JSONB, server_default=EMPTY_LIST)
    spam_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    status: Mapped[str] = mapped_column(Text, default="rascunho", server_default="rascunho")
    motivo: Mapped[str | None] = mapped_column(Text)
    agendada_para: Mapped[datetime | None] = mapped_column(TS)
    enviada_em: Mapped[datetime | None] = mapped_column(TS)
    message_id: Mapped[str | None] = mapped_column(Text, index=True)
    tentativas: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    bounce_tipo: Mapped[str | None] = mapped_column(Text)  # hard | soft
    bounce_em: Mapped[datetime | None] = mapped_column(TS)
    simulada: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    criado_em: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class InboundReply(Base):
    __tablename__ = "inbound_reply"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    outbound_id: Mapped[int] = mapped_column(ForeignKey("outbound_message.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"), index=True)
    message_key: Mapped[str] = mapped_column(Text, unique=True)
    classe: Mapped[str] = mapped_column(Text)
    acao: Mapped[str | None] = mapped_column(Text)
    trecho: Mapped[str] = mapped_column(Text, default="")
    retorno_em: Mapped[datetime | None] = mapped_column(TS)
    recebido_em: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class Deal(Base):
    __tablename__ = "deal"
    __table_args__ = (
        UniqueConstraint("workspace_id", "contact_id"),
        CheckConstraint("estagio in ('novo','interessado','reuniao','proposta','ganho','perdido','nutricao')",
                        name="ck_deal_estagio"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("company.id", ondelete="SET NULL"))
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"))
    estagio: Mapped[str] = mapped_column(Text, default="novo", server_default="novo")
    outbound_id: Mapped[int | None] = mapped_column(ForeignKey("outbound_message.id", ondelete="SET NULL"))
    reuniao_em: Mapped[datetime | None] = mapped_column(TS)
    aprendizado: Mapped[str | None] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class Task(Base):
    __tablename__ = "task"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    deal_id: Mapped[int | None] = mapped_column(ForeignKey("deal.id", ondelete="CASCADE"))
    descricao: Mapped[str] = mapped_column(Text)
    vence_em: Mapped[datetime | None] = mapped_column(TS)
    feita: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    criado_em: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class CrmEvent(Base):
    """Cada envio ao CRM externo (webhook). A chave de idempotência impede duplicar a oportunidade."""

    __tablename__ = "crm_event"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    deal_id: Mapped[int | None] = mapped_column(ForeignKey("deal.id", ondelete="SET NULL"))
    chave: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(Text, default="pendente")  # pendente | enviado | falhou | sem_destino
    http_status: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_OBJ)
    tentativas: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    criado_em: Mapped[datetime] = mapped_column(TS, server_default=func.now())


# ---------------- Entrada simples: extensão do navegador ----------------
class ExtensionToken(Base):
    """Chave da extensão do navegador. Só o hash fica guardado; a chave aparece uma única vez, ao ser criada."""

    __tablename__ = "extension_token"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    nome: Mapped[str] = mapped_column(Text, default="Extensão do navegador")
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    criado_em: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    ultimo_uso: Mapped[datetime | None] = mapped_column(TS)
    revogado: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
