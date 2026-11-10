"""chave da extensao do navegador

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-10 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'extension_token',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('nome', sa.Text(), nullable=False),
        sa.Column('token_hash', sa.Text(), nullable=False),
        sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('ultimo_uso', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revogado', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_index(op.f('ix_extension_token_workspace_id'), 'extension_token', ['workspace_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_extension_token_workspace_id'), table_name='extension_token')
    op.drop_table('extension_token')
