"""add Q1 V2 journey-to-project binding

Revision ID: 0009_q1_v2_journey_projects
Revises: 0008_siteformo_journey_identity_v1
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009_q1_v2_journey_projects"
down_revision = "0008_siteformo_journey_identity_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "siteformo_journey_projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("siteformo_visitor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("handoff_id", sa.String(length=128), nullable=True),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["siteformo_visitor_id"], ["siteformo_visitors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id"),
    )
    op.create_index("ix_siteformo_journey_projects_siteformo_visitor_id", "siteformo_journey_projects", ["siteformo_visitor_id"])
    op.create_index(
        "uq_siteformo_journey_projects_current_visitor",
        "siteformo_journey_projects",
        ["siteformo_visitor_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )


def downgrade() -> None:
    op.drop_index("uq_siteformo_journey_projects_current_visitor", table_name="siteformo_journey_projects")
    op.drop_index("ix_siteformo_journey_projects_siteformo_visitor_id", table_name="siteformo_journey_projects")
    op.drop_table("siteformo_journey_projects")
