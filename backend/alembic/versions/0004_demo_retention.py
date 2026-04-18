"""separate demo ttl from retained master storage

Revision ID: 0004_demo_retention
Revises: 0003_funnel_followups
Create Date: 2026-04-18
"""
from alembic import op
import sqlalchemy as sa

revision = '0004_demo_retention'
down_revision = '0003_funnel_followups'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('requests', sa.Column('master_storage_key', sa.Text(), nullable=True))
    op.add_column('requests', sa.Column('retention_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_requests_retention_expires_at', 'requests', ['retention_expires_at'])


def downgrade() -> None:
    op.drop_index('ix_requests_retention_expires_at', table_name='requests')
    op.drop_column('requests', 'retention_expires_at')
    op.drop_column('requests', 'master_storage_key')
