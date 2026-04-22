"""initial

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-13
"""
from alembic import op
import sqlalchemy as sa

revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'requests',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('request_type', sa.String(length=32), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=False, index=True),
        sa.Column('source_url', sa.Text(), nullable=True),
        sa.Column('business_description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False, index=True),
        sa.Column('demo_token', sa.String(length=255), nullable=True, unique=True, index=True),
        sa.Column('demo_storage_key', sa.Text(), nullable=True),
        sa.Column('demo_url', sa.Text(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column('fingerprint', sa.String(length=255), nullable=True),
        sa.Column('ip_hash', sa.String(length=128), nullable=False),
        sa.Column('user_identity_hash', sa.String(length=128), nullable=False, index=True),
        sa.Column('attempt_number', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('generation_metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        'user_usage',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('email_normalized', sa.String(length=320), nullable=False, index=True),
        sa.Column('fingerprint', sa.String(length=255), nullable=True),
        sa.Column('ip_hash', sa.String(length=128), nullable=False),
        sa.Column('user_identity_hash', sa.String(length=128), nullable=False, unique=True, index=True),
        sa.Column('free_attempts_used', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('first_request_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_request_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        'jobs',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('job_type', sa.String(length=64), nullable=False, index=True),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False, index=True),
        sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), index=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        'demo_assets',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('request_id', sa.String(length=36), sa.ForeignKey('requests.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('storage_key', sa.Text(), nullable=False),
        sa.Column('asset_type', sa.String(length=32), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        'event_logs',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('request_id', sa.String(length=36), sa.ForeignKey('requests.id', ondelete='CASCADE'), nullable=True, index=True),
        sa.Column('event_name', sa.String(length=128), nullable=False, index=True),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('event_logs')
    op.drop_table('demo_assets')
    op.drop_table('jobs')
    op.drop_table('user_usage')
    op.drop_table('requests')
