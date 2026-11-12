"""campanha manual

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-10 14:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'campaign',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('nome', sa.Text(), nullable=False),
        sa.Column('assunto', sa.Text(), server_default='', nullable=False),
        sa.Column('formato', sa.Text(), server_default='html', nullable=False),
        sa.Column('corpo_html', sa.Text(), nullable=True),
        sa.Column('corpo_texto', sa.Text(), server_default='', nullable=False),
        sa.Column('status', sa.Text(), server_default='rascunho', nullable=False),
        sa.Column('criado_por', sa.Integer(), nullable=True),
        sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('atualizado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("formato in ('html','texto')", name='ck_campaign_formato'),
        sa.ForeignKeyConstraint(['criado_por'], ['app_user.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_campaign_workspace_id'), 'campaign', ['workspace_id'], unique=False)
    op.add_column('outbound_message', sa.Column('corpo_html', sa.Text(), nullable=True))
    op.add_column('outbound_message', sa.Column('campaign_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_outbound_message_campaign_id'), 'outbound_message', ['campaign_id'], unique=False)
    op.create_foreign_key('fk_outbound_campaign', 'outbound_message', 'campaign', ['campaign_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('fk_outbound_campaign', 'outbound_message', type_='foreignkey')
    op.drop_index(op.f('ix_outbound_message_campaign_id'), table_name='outbound_message')
    op.drop_column('outbound_message', 'campaign_id')
    op.drop_column('outbound_message', 'corpo_html')
    op.drop_index(op.f('ix_campaign_workspace_id'), table_name='campaign')
    op.drop_table('campaign')
