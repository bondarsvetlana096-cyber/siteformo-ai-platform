"""add guided lead nurturing fields

Revision ID: 0005_guided_lead_nurturing
Revises: 0004_demo_retention
Create Date: 2026-04-24
"""
from alembic import op

revision = "0005_guided_lead_nurturing"
down_revision = "0004_demo_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS contact_channel VARCHAR(50)")
    op.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS is_hot BOOLEAN DEFAULT false")
    op.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS followup_stage INTEGER DEFAULT 0")
    op.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS last_contacted TIMESTAMPTZ")
    op.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS history JSONB DEFAULT '[]'::jsonb")
    op.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS estimate JSONB")
    op.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS offer_url VARCHAR(500)")


def downgrade() -> None:
    op.execute("ALTER TABLE leads DROP COLUMN IF EXISTS offer_url")
    op.execute("ALTER TABLE leads DROP COLUMN IF EXISTS estimate")
    op.execute("ALTER TABLE leads DROP COLUMN IF EXISTS history")
    op.execute("ALTER TABLE leads DROP COLUMN IF EXISTS last_contacted")
    op.execute("ALTER TABLE leads DROP COLUMN IF EXISTS followup_stage")
    op.execute("ALTER TABLE leads DROP COLUMN IF EXISTS is_hot")
    op.execute("ALTER TABLE leads DROP COLUMN IF EXISTS contact_channel")
