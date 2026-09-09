"""add neutral SiteFormo Journey Identity V1

Revision ID: 0008_siteformo_journey_identity_v1
Revises: 0007_assistant_core_v1
Create Date: 2026-09-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0008_siteformo_journey_identity_v1"
down_revision = "0007_assistant_core_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "siteformo_visitors",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credential_hash"),
    )
    op.add_column(
        "assistant_visitors",
        sa.Column("siteformo_visitor_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        """
        INSERT INTO siteformo_visitors (id, credential_hash, created_at, updated_at)
        SELECT id, credential_hash, created_at, updated_at
        FROM assistant_visitors
        """
    )
    op.execute("UPDATE assistant_visitors SET siteformo_visitor_id = id")
    op.alter_column("assistant_visitors", "siteformo_visitor_id", nullable=False)
    op.create_foreign_key(
        "assistant_visitors_siteformo_visitor_id_fkey",
        "assistant_visitors",
        "siteformo_visitors",
        ["siteformo_visitor_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "assistant_visitors_siteformo_visitor_id_key",
        "assistant_visitors",
        ["siteformo_visitor_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "assistant_visitors_siteformo_visitor_id_key", "assistant_visitors", type_="unique"
    )
    op.drop_constraint(
        "assistant_visitors_siteformo_visitor_id_fkey", "assistant_visitors", type_="foreignkey"
    )
    op.drop_column("assistant_visitors", "siteformo_visitor_id")
    op.drop_table("siteformo_visitors")
