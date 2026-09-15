"""Transactional persistence boundary for Generator V2 Phase A snapshots.

The service stores and revalidates the complete immutable typed snapshot.  It
does not read an Order to reconstruct authority and has no worker/provider
side effects.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.generator_v2 import GeneratorV2Snapshot
from app.services.generator_v2_contract import (
    GeneratorV2InputSnapshotV1,
    GeneratorV2JobPayloadV1,
    GeneratorV2SelectedDesignIdentityV1,
    GeneratorV2SnapshotResultV1,
    generator_v2_implementation_operation_key,
    validate_generator_v2_snapshot_identity,
)


SnapshotPersistenceStatus = Literal["READY", "NOT_FOUND", "CORRUPT_OR_MISMATCH"]
SnapshotPersistenceReason = Literal[
    "snapshot_not_found", "snapshot_schema_invalid", "snapshot_hash_mismatch",
    "snapshot_index_mismatch", "snapshot_order_mismatch", "transaction_failed",
    "duplicate_identity_conflict", "unsupported_contract_version",
    "context_hash_mismatch", "projection_hash_mismatch", "site_plan_hash_mismatch",
    "planner_operation_mismatch", "design_identity_mismatch", "interaction_identity_mismatch",
]


class GeneratorV2SnapshotPersistenceError(RuntimeError):
    """Sanitized persistence failure; raw database details never leave the boundary."""

    code = "transaction_failed"


class GeneratorV2SnapshotPersistenceResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: SnapshotPersistenceStatus
    reason_codes: tuple[SnapshotPersistenceReason, ...] = ()
    snapshot_id: str | None = None
    order_id: str | None = None
    generator_input_hash: str | None = None
    implementation_operation_key: str | None = None
    snapshot: GeneratorV2InputSnapshotV1 | None = None


CurrentSnapshotStatus = Literal["CURRENT", "STALE", "CORRUPT_OR_MISMATCH"]


class GeneratorV2CurrentSnapshotResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: CurrentSnapshotStatus
    reason_codes: tuple[SnapshotPersistenceReason, ...] = ()
    snapshot_id: str | None = None
    snapshot: GeneratorV2InputSnapshotV1 | None = None


def _indexed_values(snapshot: GeneratorV2InputSnapshotV1, operation_key: str) -> dict:
    return {
        "order_id": snapshot.order_id,
        "journey_project_id": snapshot.journey_project_id,
        "generator_input_hash": snapshot.generator_input_hash,
        "implementation_operation_key": operation_key,
        "generation_context_hash": snapshot.generation_context_hash,
        "constraint_projection_hash": snapshot.constraint_projection_hash,
        "site_plan_hash": snapshot.site_plan_hash,
        "planner_operation_key": snapshot.planner_operation_key,
        "generator_input_contract_version": snapshot.contract_version,
        "implementation_strategy_version": "v1",
        "generation_context_contract_version": snapshot.generation_context_contract_version,
        "constraint_projection_contract_version": snapshot.constraint_projection_contract_version,
        "site_plan_contract_version": snapshot.site_plan_contract_version,
        "planner_contract_version": snapshot.planner_contract_version,
        "policy_version": snapshot.policy_version,
        "validator_version": snapshot.validator_version,
    }


def _parse_and_verify_row(row: GeneratorV2Snapshot) -> tuple[GeneratorV2InputSnapshotV1 | None, SnapshotPersistenceReason | None]:
    try:
        snapshot = GeneratorV2InputSnapshotV1.model_validate(row.snapshot_json)
    except Exception:
        return None, "snapshot_schema_invalid"
    expected = _indexed_values(snapshot, row.implementation_operation_key)
    if any(getattr(row, key) != value for key, value in expected.items()):
        return None, "snapshot_index_mismatch"
    if generator_v2_implementation_operation_key(snapshot) != row.implementation_operation_key:
        return None, "snapshot_hash_mismatch"
    return snapshot, None


def _result_from_row(row: GeneratorV2Snapshot) -> GeneratorV2SnapshotPersistenceResultV1:
    snapshot, reason = _parse_and_verify_row(row)
    if reason:
        return GeneratorV2SnapshotPersistenceResultV1(
            status="CORRUPT_OR_MISMATCH", reason_codes=(reason,), snapshot_id=row.id, order_id=row.order_id,
        )
    return GeneratorV2SnapshotPersistenceResultV1(
        status="READY", snapshot_id=row.id, order_id=row.order_id,
        generator_input_hash=row.generator_input_hash,
        implementation_operation_key=row.implementation_operation_key, snapshot=snapshot,
    )


def persist_generator_v2_snapshot(
    db: Session, snapshot: GeneratorV2InputSnapshotV1,
) -> GeneratorV2SnapshotPersistenceResultV1:
    """Insert an immutable snapshot, resolving duplicate races by unique key."""
    try:
        canonical = GeneratorV2InputSnapshotV1.model_validate(snapshot.model_dump(mode="json"))
    except Exception:
        return GeneratorV2SnapshotPersistenceResultV1(status="CORRUPT_OR_MISMATCH", reason_codes=("snapshot_schema_invalid",))
    operation_key = generator_v2_implementation_operation_key(canonical)
    values = _indexed_values(canonical, operation_key)
    row = GeneratorV2Snapshot(**values, snapshot_json=canonical.model_dump(mode="json"))
    try:
        db.add(row)
        db.commit()
        db.refresh(row)
        return _result_from_row(row)
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(GeneratorV2Snapshot).where(
            GeneratorV2Snapshot.order_id == canonical.order_id,
            GeneratorV2Snapshot.generator_input_hash == canonical.generator_input_hash,
            GeneratorV2Snapshot.implementation_operation_key == operation_key,
        ))
        if existing is None:
            return GeneratorV2SnapshotPersistenceResultV1(
                status="CORRUPT_OR_MISMATCH", reason_codes=("duplicate_identity_conflict",)
            )
        return _result_from_row(existing)
    except SQLAlchemyError as exc:
        db.rollback()
        raise GeneratorV2SnapshotPersistenceError() from None


def get_or_create_generator_v2_snapshot(
    db: Session, snapshot: GeneratorV2InputSnapshotV1,
) -> GeneratorV2SnapshotPersistenceResultV1:
    return persist_generator_v2_snapshot(db, snapshot)


def get_generator_v2_snapshot(
    db: Session, snapshot_id: str, *, expected_order_id: str | None = None,
) -> GeneratorV2SnapshotPersistenceResultV1:
    row = db.scalar(select(GeneratorV2Snapshot).where(GeneratorV2Snapshot.id == snapshot_id))
    if row is None:
        return GeneratorV2SnapshotPersistenceResultV1(status="NOT_FOUND", reason_codes=("snapshot_not_found",))
    if expected_order_id is not None and row.order_id != expected_order_id:
        return GeneratorV2SnapshotPersistenceResultV1(
            status="CORRUPT_OR_MISMATCH", reason_codes=("snapshot_order_mismatch",), snapshot_id=row.id,
        )
    return _result_from_row(row)


def verify_generator_v2_snapshot(
    db: Session, snapshot_id: str, *, expected_order_id: str,
    generation_context_hash: str, constraint_projection_hash: str, site_plan_hash: str,
    planner_operation_key: str, selected_design: GeneratorV2SelectedDesignIdentityV1,
) -> GeneratorV2CurrentSnapshotResultV1:
    result = get_generator_v2_snapshot(db, snapshot_id, expected_order_id=expected_order_id)
    if result.status != "READY" or result.snapshot is None:
        return GeneratorV2CurrentSnapshotResultV1(
            status="CORRUPT_OR_MISMATCH", reason_codes=result.reason_codes, snapshot_id=snapshot_id,
        )
    identity = validate_generator_v2_snapshot_identity(
        result.snapshot, generation_context_hash=generation_context_hash,
        constraint_projection_hash=constraint_projection_hash, site_plan_hash=site_plan_hash,
        planner_operation_key=planner_operation_key, selected_design=selected_design,
    )
    if identity.status == "READY":
        return GeneratorV2CurrentSnapshotResultV1(status="CURRENT", snapshot_id=snapshot_id, snapshot=result.snapshot)
    return GeneratorV2CurrentSnapshotResultV1(
        status="STALE", reason_codes=(identity.reason_codes[0],), snapshot_id=snapshot_id, snapshot=result.snapshot,
    )


def build_generator_v2_job_payload(
    snapshot_id: str, snapshot: GeneratorV2InputSnapshotV1, *, approved_revision_version: str | None = None,
) -> GeneratorV2JobPayloadV1:
    """Prepare (but do not enqueue) the closed future job reference."""
    return GeneratorV2JobPayloadV1(
        contract_version="v1", order_id=snapshot.order_id, journey_project_id=snapshot.journey_project_id,
        generator_input_hash=snapshot.generator_input_hash, snapshot_id=snapshot_id,
        site_plan_hash=snapshot.site_plan_hash, generation_context_hash=snapshot.generation_context_hash,
        constraint_projection_hash=snapshot.constraint_projection_hash,
        implementation_operation_key=generator_v2_implementation_operation_key(snapshot),
        generation_context_contract_version=snapshot.generation_context_contract_version,
        constraint_projection_contract_version=snapshot.constraint_projection_contract_version,
        site_plan_contract_version=snapshot.site_plan_contract_version,
        implementation_strategy_version="v1", approved_revision_version=approved_revision_version,
    )
