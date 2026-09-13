"""add Journey-owned payment boundary v2

Revision ID: 0010_payment_boundary_v2
Revises: 0009_q1_v2_journey_projects
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010_payment_boundary_v2"
down_revision = "0009_q1_v2_journey_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("deposit_status", sa.String(32), server_default="not_started", nullable=False))
    op.add_column("orders", sa.Column("deposit_paid_at", sa.DateTime(timezone=True)))
    op.add_column("orders", sa.Column("deposit_amount_cents", sa.Integer()))
    op.add_column("orders", sa.Column("deposit_currency", sa.String(3)))
    op.add_column("orders", sa.Column("brief_confirmed_at", sa.DateTime(timezone=True)))
    op.add_column("orders", sa.Column("legal_terms_version", sa.String(128)))
    op.add_column("orders", sa.Column("legal_confirmed_at", sa.DateTime(timezone=True)))
    op.add_column("orders", sa.Column("prepayment_summary_email_status", sa.String(16), server_default="pending", nullable=False))
    op.add_column("orders", sa.Column("prepayment_summary_email_sent_at", sa.DateTime(timezone=True)))
    op.create_table(
        "payment_attempts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("order_id", sa.String(36), nullable=False),
        sa.Column("siteformo_visitor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_type", sa.String(32), nullable=False),
        sa.Column("stripe_checkout_session_id", sa.String(255)),
        sa.Column("stripe_payment_intent_id", sa.String(255)),
        sa.Column("checkout_url", sa.Text()),
        sa.Column("scope_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("scope_version", sa.String(64), nullable=False),
        sa.Column("scope_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("base_package", sa.String(32), nullable=False),
        sa.Column("base_package_price_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("expected_total_amount_cents", sa.Integer(), nullable=False),
        sa.Column("expected_deposit_amount_cents", sa.Integer(), nullable=False),
        sa.Column("confirmed_addons_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("legal_terms_version", sa.String(128), nullable=False),
        sa.Column("legal_confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["siteformo_visitor_id"], ["siteformo_visitors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_checkout_session_id"),
    )
    op.create_index("ix_payment_attempts_order_id", "payment_attempts", ["order_id"])
    op.create_index("ix_payment_attempts_siteformo_visitor_id", "payment_attempts", ["siteformo_visitor_id"])
    op.create_index("ix_payment_attempts_status", "payment_attempts", ["status"])
    op.create_index(
        "uq_payment_attempts_active_order",
        "payment_attempts", ["order_id"], unique=True,
        postgresql_where=sa.text("status IN ('creating','checkout_created','pending')"),
    )
    op.create_table(
        "stripe_webhook_events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("stripe_event_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("processing_result", sa.String(64)),
        sa.Column("payment_attempt_id", sa.String(36)),
        sa.Column("order_id", sa.String(36)),
        sa.ForeignKeyConstraint(["payment_attempt_id"], ["payment_attempts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_event_id"),
    )


def downgrade() -> None:
    op.drop_table("stripe_webhook_events")
    op.drop_index("uq_payment_attempts_active_order", table_name="payment_attempts")
    op.drop_index("ix_payment_attempts_status", table_name="payment_attempts")
    op.drop_index("ix_payment_attempts_siteformo_visitor_id", table_name="payment_attempts")
    op.drop_index("ix_payment_attempts_order_id", table_name="payment_attempts")
    op.drop_table("payment_attempts")
    for name in (
        "prepayment_summary_email_sent_at", "prepayment_summary_email_status",
        "legal_confirmed_at", "legal_terms_version", "brief_confirmed_at",
        "deposit_currency", "deposit_amount_cents", "deposit_paid_at", "deposit_status",
    ):
        op.drop_column("orders", name)
