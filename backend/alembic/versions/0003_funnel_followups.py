"""add funnel followups

Revision ID: 0003_funnel_followups
Revises: 0002_contact_channels
Create Date: 2026-04-18
"""
from alembic import op
import sqlalchemy as sa

revision = '0003_funnel_followups'
down_revision = '0002_contact_channels'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('requests', sa.Column('demo_opened_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('requests', sa.Column('demo_cta_clicked_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('requests', sa.Column('main_form_started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('requests', sa.Column('main_form_completed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('requests', sa.Column('payment_started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('requests', sa.Column('payment_completed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('requests', sa.Column('last_follow_up_sent_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('requests', sa.Column('last_follow_up_reason', sa.String(length=64), nullable=True))
    op.add_column('requests', sa.Column('follow_up_count', sa.Integer(), nullable=False, server_default='0'))

    op.create_index('ix_requests_demo_opened_at', 'requests', ['demo_opened_at'])
    op.create_index('ix_requests_demo_cta_clicked_at', 'requests', ['demo_cta_clicked_at'])
    op.create_index('ix_requests_main_form_started_at', 'requests', ['main_form_started_at'])
    op.create_index('ix_requests_main_form_completed_at', 'requests', ['main_form_completed_at'])
    op.create_index('ix_requests_payment_started_at', 'requests', ['payment_started_at'])
    op.create_index('ix_requests_payment_completed_at', 'requests', ['payment_completed_at'])


def downgrade() -> None:
    op.drop_index('ix_requests_payment_completed_at', table_name='requests')
    op.drop_index('ix_requests_payment_started_at', table_name='requests')
    op.drop_index('ix_requests_main_form_completed_at', table_name='requests')
    op.drop_index('ix_requests_main_form_started_at', table_name='requests')
    op.drop_index('ix_requests_demo_cta_clicked_at', table_name='requests')
    op.drop_index('ix_requests_demo_opened_at', table_name='requests')
    op.drop_column('requests', 'follow_up_count')
    op.drop_column('requests', 'last_follow_up_reason')
    op.drop_column('requests', 'last_follow_up_sent_at')
    op.drop_column('requests', 'payment_completed_at')
    op.drop_column('requests', 'payment_started_at')
    op.drop_column('requests', 'main_form_completed_at')
    op.drop_column('requests', 'main_form_started_at')
    op.drop_column('requests', 'demo_cta_clicked_at')
    op.drop_column('requests', 'demo_opened_at')
