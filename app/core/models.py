"""Tabelas da fundação (Sprint 0, com as correções F1/F3/F4 do Prompt Mestre v3.1).

Multi-tenant: toda tabela de negócio tem workspace_id.
"""
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

TS = TIMESTAMP(timezone=True)
TextArray = ARRAY(Text)
EMPTY_ARRAY = text("'{}'")
EMPTY_OBJ = text("'{}'::jsonb")


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    __tablename__ = "workspace"
    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(Text, unique=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_OBJ)
    limites: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_OBJ)
    chaves_api_enc: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_OBJ)
    criado_em: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class AppUser(Base):
    __tablename__ = "app_user"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True)
    nome: Mapped[str] = mapped_column(Text, default="")
    senha_hash: Mapped[str] = mapped_column(Text)
    ativo: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    criado_em: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class WorkspaceMember(Base):
    __tablename__ = "workspace_member"
    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id"),
        CheckConstraint("papel in ('admin','operador','aprovador')", name="ck_member_papel"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"))
    papel: Mapped[str] = mapped_column(Text, default="admin")


class IcpProfile(Base):
    __tablename__ = "icp_profile"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(Text)
    cnaes: Mapped[list[str]] = mapped_column(TextArray, server_default=EMPTY_ARRAY)
    ufs: Mapped[list[str]] = mapped_column(TextArray, server_default=EMPTY_ARRAY)
    municipios: Mapped[list[str]] = mapped_column(TextArray, server_default=EMPTY_ARRAY)
    portes: Mapped[list[str]] = mapped_column(TextArray, server_default=EMPTY_ARRAY)
    termos_maps: Mapped[list[str]] = mapped_column(TextArray, server_default=EMPTY_ARRAY)
    departamentos_alvo: Mapped[list[str]] = mapped_column(TextArray, server_default=EMPTY_ARRAY)
    criado_em: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class Department(Base):
    __tablename__ = "department"
    __table_args__ = (UniqueConstraint("workspace_id", "nome"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(Text)
    email_patterns: Mapped[list[str]] = mapped_column(TextArray, server_default=EMPTY_ARRAY)
    cargos: Mapped[list[str]] = mapped_column(TextArray, server_default=EMPTY_ARRAY)
    necessidades_previstas: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))


class JobRun(Base):
    __tablename__ = "job_run"
    __table_args__ = (
        # Regra 6: mesmo tipo + mesmos parâmetros não roda duas vezes (exceto se o anterior falhou).
        Index(
            "uq_job_run_dedupe",
            "tipo",
            "parametros_hash",
            "workspace_id",
            unique=True,
            postgresql_where=text("status <> 'falhou'"),
            postgresql_nulls_not_distinct=True,
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    tipo: Mapped[str] = mapped_column(Text)
    parametros_hash: Mapped[str] = mapped_column(Text)
    parametros: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_OBJ)
    status: Mapped[str] = mapped_column(Text, default="em_andamento")
    contadores: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_OBJ)
    custo_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0, server_default=text("0"))
    iniciado_em: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    finalizado_em: Mapped[datetime | None] = mapped_column(TS)


class Suppression(Base):
    __tablename__ = "suppression"
    __table_args__ = (UniqueConstraint("workspace_id", "email_ou_dominio"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    email_ou_dominio: Mapped[str] = mapped_column(Text)
    motivo: Mapped[str] = mapped_column(Text)
    origem: Mapped[str] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class ComplianceRecord(Base):
    __tablename__ = "compliance_record"
    __table_args__ = (
        UniqueConstraint("entidade_tipo", "entidade_id", "origem_dado"),
        Index("ix_compliance_entity", "entidade_tipo", "entidade_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    entidade_tipo: Mapped[str] = mapped_column(Text)
    entidade_id: Mapped[int] = mapped_column(Integer)
    origem_dado: Mapped[str] = mapped_column(Text)
    finalidade: Mapped[str] = mapped_column(Text)
    base_legal: Mapped[str] = mapped_column(Text)
    coletado_em: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    retencao_ate: Mapped[datetime | None] = mapped_column(TS)
    historico_oposicao: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))


class Company(Base):
    __tablename__ = "company"
    __table_args__ = (
        Index(
            "uq_company_cnpj",
            "workspace_id",
            "cnpj",
            unique=True,
            postgresql_where=text("cnpj IS NOT NULL"),
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    cnpj: Mapped[str | None] = mapped_column(Text)
    razao_social: Mapped[str | None] = mapped_column(Text)
    nome_fantasia: Mapped[str | None] = mapped_column(Text)
    dominio: Mapped[str | None] = mapped_column(Text, index=True)
    site: Mapped[str | None] = mapped_column(Text)
    cnae_principal: Mapped[str | None] = mapped_column(Text)
    cnae_secundarios: Mapped[list[str]] = mapped_column(TextArray, server_default=EMPTY_ARRAY)
    municipio: Mapped[str | None] = mapped_column(Text)
    uf: Mapped[str | None] = mapped_column(Text)
    porte: Mapped[str | None] = mapped_column(Text)
    data_abertura: Mapped[Any | None] = mapped_column(Date)
    situacao_cadastral: Mapped[str | None] = mapped_column(Text)
    liveness_score: Mapped[int | None] = mapped_column(Integer)
    fit_score: Mapped[int | None] = mapped_column(Integer)
    setor_humano: Mapped[str | None] = mapped_column(Text)
    gatilhos_previaveis: Mapped[list[str]] = mapped_column(TextArray, server_default=EMPTY_ARRAY)
    criado_em: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class Contact(Base):
    __tablename__ = "contact"
    __table_args__ = (UniqueConstraint("workspace_id", "email"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("company.id", ondelete="SET NULL"), index=True)
    nome_completo: Mapped[str | None] = mapped_column(Text)
    primeiro_nome: Mapped[str | None] = mapped_column(Text)
    forma_tratamento: Mapped[str | None] = mapped_column(Text)
    cargo: Mapped[str | None] = mapped_column(Text)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("department.id", ondelete="SET NULL"))
    email: Mapped[str | None] = mapped_column(Text)
    email_type: Mapped[str | None] = mapped_column(Text)
    email_status: Mapped[str] = mapped_column(Text, default="nao_verificado", server_default="nao_verificado")
    relationship_score: Mapped[int | None] = mapped_column(Integer)
    origem: Mapped[str | None] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint("confianca >= 0 AND confianca <= 1", name="ck_evidence_confianca"),
        Index("ix_evidence_entity", "entidade_tipo", "entidade_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    entidade_tipo: Mapped[str] = mapped_column(Text)
    entidade_id: Mapped[int] = mapped_column(Integer)
    campo: Mapped[str] = mapped_column(Text)
    valor: Mapped[str | None] = mapped_column(Text)
    fonte: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    confianca: Mapped[float] = mapped_column(Numeric(4, 3))
    coletado_em: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class Verification(Base):
    __tablename__ = "verification"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"), index=True)
    camada: Mapped[str] = mapped_column(Text)
    resultado: Mapped[str] = mapped_column(Text)
    detalhes: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_OBJ)
    provedor: Mapped[str | None] = mapped_column(Text)
    verificado_em: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class ContactRole(Base):
    __tablename__ = "contact_role"
    __table_args__ = (
        CheckConstraint(
            "papel in ('decisor','influenciador','usuario','orcamento','aprovador','gatekeeper','socio','administrador')",
            name="ck_contact_role_papel",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"), index=True)
    papel: Mapped[str] = mapped_column(Text)
    confianca: Mapped[float] = mapped_column(Numeric(4, 3), default=0.5)
    fonte: Mapped[str | None] = mapped_column(Text)
