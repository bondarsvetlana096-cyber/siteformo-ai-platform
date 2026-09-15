"""Immutable Generator V2 snapshot persistence model.

This table stores the complete, already validated Phase A snapshot.  It is
deliberately independent of generation workers and legacy Order authority.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, UniqueConstraint, event, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.order import Order  # noqa: F401 - registers the FK target in shared metadata


_SNAPSHOT_JSON = JSON().with_variant(JSONB(astext_type=None), "postgresql")


class GeneratorV2Snapshot(Base):
    __tablename__ = "generator_v2_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    order_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False
    )
    journey_project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    generator_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    implementation_operation_key: Mapped[str] = mapped_column(String(64), nullable=False)
    generation_context_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    constraint_projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    site_plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    planner_operation_key: Mapped[str] = mapped_column(String(64), nullable=False)
    generator_input_contract_version: Mapped[str] = mapped_column(String(16), nullable=False)
    implementation_strategy_version: Mapped[str] = mapped_column(String(16), nullable=False)
    generation_context_contract_version: Mapped[str] = mapped_column(String(16), nullable=False)
    constraint_projection_contract_version: Mapped[str] = mapped_column(String(16), nullable=False)
    site_plan_contract_version: Mapped[str] = mapped_column(String(16), nullable=False)
    planner_contract_version: Mapped[str] = mapped_column(String(16), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(16), nullable=False)
    validator_version: Mapped[str] = mapped_column(String(16), nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(_SNAPSHOT_JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "order_id", "generator_input_hash", "implementation_operation_key",
            name="uq_generator_v2_snapshot_identity",
        ),
        Index("ix_generator_v2_snapshots_order_id", "order_id"),
        Index("ix_generator_v2_snapshots_operation_key", "implementation_operation_key"),
    )


@event.listens_for(GeneratorV2Snapshot, "before_update")
def _reject_snapshot_update(mapper, connection, target) -> None:  # pragma: no cover - exercised through service tests
    raise ValueError("generator_v2_snapshot_immutable")


@event.listens_for(GeneratorV2Snapshot, "before_delete")
def _reject_snapshot_delete(mapper, connection, target) -> None:  # pragma: no cover
    raise ValueError("generator_v2_snapshot_append_only")


def ensure_generator_v2_snapshot_immutability(engine) -> None:
    """Install the PostgreSQL append-only trigger for fresh-install paths."""
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text(
            """CREATE OR REPLACE FUNCTION generator_v2_snapshots_reject_update()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'generator_v2_snapshots_are_immutable'; END;
            $$"""
        ))
        connection.execute(text(
            "DROP TRIGGER IF EXISTS generator_v2_snapshots_immutable ON generator_v2_snapshots"
        ))
        connection.execute(text(
            """CREATE TRIGGER generator_v2_snapshots_immutable
            BEFORE UPDATE OR DELETE ON generator_v2_snapshots FOR EACH ROW
            EXECUTE FUNCTION generator_v2_snapshots_reject_update()"""
        ))
