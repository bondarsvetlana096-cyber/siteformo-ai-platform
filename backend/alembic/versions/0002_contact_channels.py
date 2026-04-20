"""add contact channels

Revision ID: 0002_contact_channels
Revises: 0001_initial
Create Date: 2026-04-15
"""
from alembic import op
import sqlalchemy as sa

revision = '0002_contact_channels'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('requests', sa.Column('contact_type', sa.String(length=32), nullable=True))
    op.add_column('requests', sa.Column('contact_value', sa.String(length=320), nullable=True))
    op.add_column('requests', sa.Column('contact_confirmation_token', sa.String(length=255), nullable=True))
    op.add_column('requests', sa.Column('contact_confirmed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('requests', sa.Column('inbound_message_text', sa.Text(), nullable=True))
    op.add_column('requests', sa.Column('outbound_message_text', sa.Text(), nullable=True))

    op.execute("update requests set contact_type = 'email' where contact_type is null")
    op.execute("update requests set contact_value = email where contact_value is null")

    op.alter_column('requests', 'contact_type', nullable=False)
    op.alter_column('requests', 'contact_value', nullable=False)
    op.alter_column('requests', 'email', nullable=True)

    op.create_index('ix_requests_contact_type', 'requests', ['contact_type'])
    op.create_index('ix_requests_contact_value', 'requests', ['contact_value'])
    op.create_index('ix_requests_contact_confirmation_token', 'requests', ['contact_confirmation_token'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_requests_contact_confirmation_token', table_name='requests')
    op.drop_index('ix_requests_contact_value', table_name='requests')
    op.drop_index('ix_requests_contact_type', table_name='requests')
    op.alter_column('requests', 'email', nullable=False)
    op.drop_column('requests', 'outbound_message_text')
    op.drop_column('requests', 'inbound_message_text')
    op.drop_column('requests', 'contact_confirmed_at')
    op.drop_column('requests', 'contact_confirmation_token')
    op.drop_column('requests', 'contact_value')
    op.drop_column('requests', 'contact_type')
