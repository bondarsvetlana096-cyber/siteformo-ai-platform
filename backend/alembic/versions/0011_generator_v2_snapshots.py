"""add immutable Generator V2 snapshots

Revision ID: 0011_generator_v2_snapshots
Revises: 0010_payment_boundary_v2
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011_generator_v2_snapshots"
down_revision = "0010_payment_boundary_v2"
branch_labels = None
depends_on = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "generator_v2_snapshots",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("order_id", sa.String(128), nullable=False),
        sa.Column("journey_project_id", sa.String(128), nullable=False),
        sa.Column("generator_input_hash", sa.String(64), nullable=False),
        sa.Column("implementation_operation_key", sa.String(64), nullable=False),
        sa.Column("generation_context_hash", sa.String(64), nullable=False),
        sa.Column("constraint_projection_hash", sa.String(64), nullable=False),
        sa.Column("site_plan_hash", sa.String(64), nullable=False),
        sa.Column("planner_operation_key", sa.String(64), nullable=False),
        sa.Column("generator_input_contract_version", sa.String(16), nullable=False),
        sa.Column("implementation_strategy_version", sa.String(16), nullable=False),
        sa.Column("generation_context_contract_version", sa.String(16), nullable=False),
        sa.Column("constraint_projection_contract_version", sa.String(16), nullable=False),
        sa.Column("site_plan_contract_version", sa.String(16), nullable=False),
        sa.Column("planner_contract_version", sa.String(16), nullable=False),
        sa.Column("policy_version", sa.String(16), nullable=False),
        sa.Column("validator_version", sa.String(16), nullable=False),
        sa.Column("snapshot_json", _json_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "order_id", "generator_input_hash", "implementation_operation_key",
            name="uq_generator_v2_snapshot_identity",
        ),
    )
    op.create_index("ix_generator_v2_snapshots_order_id", "generator_v2_snapshots", ["order_id"])
    op.create_index("ix_generator_v2_snapshots_operation_key", "generator_v2_snapshots", ["implementation_operation_key"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """CREATE OR REPLACE FUNCTION generator_v2_snapshots_reject_update()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'generator_v2_snapshots_are_immutable'; END;
            $$"""
        )
        op.execute(
            """CREATE TRIGGER generator_v2_snapshots_immutable
            BEFORE UPDATE OR DELETE ON generator_v2_snapshots FOR EACH ROW
            EXECUTE FUNCTION generator_v2_snapshots_reject_update()"""
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS generator_v2_snapshots_immutable ON generator_v2_snapshots")
        op.execute("DROP FUNCTION IF EXISTS generator_v2_snapshots_reject_update()")
    op.drop_index("ix_generator_v2_snapshots_operation_key", table_name="generator_v2_snapshots")
    op.drop_index("ix_generator_v2_snapshots_order_id", table_name="generator_v2_snapshots")
    op.drop_table("generator_v2_snapshots")
