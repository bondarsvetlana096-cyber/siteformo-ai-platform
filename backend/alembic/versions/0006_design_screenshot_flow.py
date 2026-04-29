"""add design screenshot flow fields

Revision ID: 0006_design_screenshot_flow
Revises: 0005_guided_lead_nurturing
Create Date: 2026-04-29
"""
from alembic import op

revision = "0006_design_screenshot_flow"
down_revision = "0005_guided_lead_nurturing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS extended_brief JSONB")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS design_status VARCHAR(64)")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS design_previews JSONB")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS logo_previews JSONB")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS preview_generation_payload JSONB")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS selected_design_id VARCHAR(64)")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS selected_design_label VARCHAR(128)")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS selected_design_url TEXT")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS selected_screenshot_url TEXT")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS design_approved_at TIMESTAMPTZ")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS refund_window_started_at TIMESTAMPTZ")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS refund_window_expires_at TIMESTAMPTZ")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS generation_status VARCHAR(64)")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS full_generation_started_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS full_generation_started_at")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS generation_status")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS refund_window_expires_at")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS refund_window_started_at")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS design_approved_at")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS selected_screenshot_url")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS selected_design_url")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS selected_design_label")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS selected_design_id")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS preview_generation_payload")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS logo_previews")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS design_previews")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS design_status")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS extended_brief")
