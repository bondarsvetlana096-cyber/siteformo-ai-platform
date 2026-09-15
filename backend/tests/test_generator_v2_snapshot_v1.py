from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.generator_v2 import GeneratorV2Snapshot
from app.models.order import ClientProfile, Order  # noqa: F401
from app.services.generator_v2_snapshot_service import (
    GeneratorV2SnapshotPersistenceError,
    build_generator_v2_job_payload,
    get_generator_v2_snapshot,
    get_or_create_generator_v2_snapshot,
    persist_generator_v2_snapshot,
    verify_generator_v2_snapshot,
)
from app.services.generator_v2_contract import (
    GeneratorV2SelectedDesignIdentityV1,
    build_generator_v2_input_snapshot,
)
from app.services.site_planner_constraint_projection import build_planner_constraint_projection_v1
from app.services.site_plan_validator import validate_site_plan_v1
from evals.site_planner.eval_cases import site_planner_eval_cases_v1
from test_site_planner_constraint_projection_v1 import compliant_plan


CASES = {case.case_id: case for case in site_planner_eval_cases_v1()}
REPRESENTATIVE = (
    "STARTER_LOCAL_SERVICE", "BUSINESS_THREE_PAGE", "REFERENCE_PORTFOLIO_EXPRESSIVE",
    "REFERENCE_BOOKING", "REFERENCE_ECOMMERCE", "ADVANCED_ACCOUNT_PERSISTENT", "ADVANCED_SUBTLE",
)


def _selected(context):
    return GeneratorV2SelectedDesignIdentityV1(
        design_direction=context.visual.design_direction.value,
        design_direction_contract_version="v1",
        interaction_preference=context.interaction_guidance.client_preference.value,
        interaction_preference_contract_version="v1",
    )


def _snapshot(case_id="STARTER_LOCAL_SERVICE", planner_key="a" * 64):
    context = CASES[case_id].context
    projection = build_planner_constraint_projection_v1(context)
    plan = validate_site_plan_v1(context, compliant_plan(context)).plan
    result = build_generator_v2_input_snapshot(
        context=context, projection=projection, site_plan=plan,
        planner_operation_key=planner_key, selected_design=_selected(context),
    )
    assert result.status == "READY" and result.snapshot is not None
    return result.snapshot


@pytest.fixture()
def session_maker():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def test_table_is_additive_and_fresh_install_contains_snapshot_schema():
    assert "generator_v2_snapshots" in Base.metadata.tables
    table = Base.metadata.tables["generator_v2_snapshots"]
    assert {"snapshot_json", "site_plan_hash", "generator_input_hash", "implementation_operation_key"} <= set(table.c.keys())
    assert any(const.name == "uq_generator_v2_snapshot_identity" for const in table.constraints)
    assert any(fk.ondelete == "RESTRICT" for fk in table.foreign_keys)


def test_additive_migration_upgrade_and_downgrade_on_disposable_sqlite():
    path = Path("backend/alembic/versions/0011_generator_v2_snapshots.py")
    spec = importlib.util.spec_from_file_location("generator_v2_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE orders (id VARCHAR(128) PRIMARY KEY)"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert "generator_v2_snapshots" in connection.dialect.get_table_names(connection)
        migration.downgrade()
        assert "generator_v2_snapshots" not in connection.dialect.get_table_names(connection)
        migration.upgrade()
        assert "generator_v2_snapshots" in connection.dialect.get_table_names(connection)
    engine.dispose()


def test_round_trip_revalidates_typed_full_snapshot(session_maker):
    snapshot = _snapshot()
    with session_maker() as db:
        created = persist_generator_v2_snapshot(db, snapshot)
        assert created.status == "READY"
        loaded = get_generator_v2_snapshot(db, created.snapshot_id)
        assert loaded.status == "READY"
        assert loaded.snapshot == snapshot
        assert loaded.snapshot.site_plan.model_dump(mode="json") == snapshot.site_plan.model_dump(mode="json")


def test_invalid_snapshot_is_rejected_without_database_write(session_maker):
    snapshot = _snapshot().model_copy(update={"generator_input_hash": "f" * 64})
    with session_maker() as db:
        result = persist_generator_v2_snapshot(db, snapshot)
        assert result.status == "CORRUPT_OR_MISMATCH"
        assert db.scalar(select(func.count()).select_from(GeneratorV2Snapshot)) == 0


def test_database_failure_rolls_back_without_partial_authority():
    snapshot = _snapshot()

    class FailingSession:
        rolled_back = False

        def add(self, row):
            self.row = row

        def commit(self):
            raise OperationalError("insert", {}, RuntimeError("offline"))

        def rollback(self):
            self.rolled_back = True

    db = FailingSession()
    with pytest.raises(GeneratorV2SnapshotPersistenceError):
        persist_generator_v2_snapshot(db, snapshot)
    assert db.rolled_back is True


def test_same_identity_is_idempotent_and_duplicate_race_is_database_guarded(session_maker):
    snapshot = _snapshot()
    with session_maker() as db:
        first = get_or_create_generator_v2_snapshot(db, snapshot)
        second = get_or_create_generator_v2_snapshot(db, snapshot)
        count = db.scalar(select(func.count()).select_from(GeneratorV2Snapshot))
        assert first.snapshot_id == second.snapshot_id
        assert count == 1
        with pytest.raises(IntegrityError):
            db.add(GeneratorV2Snapshot(
                id="manual-duplicate", snapshot_json=snapshot.model_dump(mode="json"),
                **{key: value for key, value in {
                    "order_id": snapshot.order_id, "journey_project_id": snapshot.journey_project_id,
                    "generator_input_hash": snapshot.generator_input_hash,
                    "implementation_operation_key": first.implementation_operation_key,
                    "generation_context_hash": snapshot.generation_context_hash,
                    "constraint_projection_hash": snapshot.constraint_projection_hash,
                    "site_plan_hash": snapshot.site_plan_hash, "planner_operation_key": snapshot.planner_operation_key,
                    "generator_input_contract_version": "v1", "implementation_strategy_version": "v1",
                    "generation_context_contract_version": "v1", "constraint_projection_contract_version": "v1",
                    "site_plan_contract_version": "v1", "planner_contract_version": "v1",
                    "policy_version": "v1", "validator_version": "v1",
                }.items()},
            ))
            db.commit()
        db.rollback()


def test_changed_authority_creates_new_historical_snapshot(session_maker):
    first = _snapshot()
    second = _snapshot(planner_key="b" * 64)
    assert first.generator_input_hash != second.generator_input_hash
    with session_maker() as db:
        a = persist_generator_v2_snapshot(db, first)
        b = persist_generator_v2_snapshot(db, second)
        assert a.snapshot_id != b.snapshot_id
        assert db.scalar(select(func.count()).select_from(GeneratorV2Snapshot)) == 2


def test_observability_timestamp_does_not_change_canonical_generator_hash():
    snapshot = _snapshot()
    altered_plan = snapshot.site_plan.model_copy(update={"created_at": snapshot.site_plan.created_at.replace(year=2030)})
    altered = snapshot.model_copy(update={"site_plan": altered_plan})
    assert altered.generator_input_hash == snapshot.generator_input_hash


def test_orm_update_and_delete_are_rejected_and_history_survives(session_maker):
    snapshot = _snapshot()
    with session_maker() as db:
        created = persist_generator_v2_snapshot(db, snapshot)
        row = db.get(GeneratorV2Snapshot, created.snapshot_id)
        row.snapshot_json = {"corrupt": True}
        with pytest.raises(ValueError, match="immutable"):
            db.commit()
        db.rollback()
        row = db.get(GeneratorV2Snapshot, created.snapshot_id)
        assert row.snapshot_json["generator_input_hash"] == snapshot.generator_input_hash
        db.delete(row)
        with pytest.raises(ValueError, match="append[_-]only"):
            db.commit()
        db.rollback()


def test_corruption_is_fail_closed(session_maker):
    snapshot = _snapshot()
    with session_maker() as db:
        created = persist_generator_v2_snapshot(db, snapshot)
        db.execute(
            update(GeneratorV2Snapshot).where(GeneratorV2Snapshot.id == created.snapshot_id).values(
                snapshot_json={"contract_version": "v1"}
            )
        )
        db.commit()
        result = get_generator_v2_snapshot(db, created.snapshot_id)
        assert result.status == "CORRUPT_OR_MISMATCH"
        assert result.reason_codes == ("snapshot_schema_invalid",)


def test_stale_current_and_cross_order_boundaries(session_maker):
    snapshot = _snapshot()
    with session_maker() as db:
        created = persist_generator_v2_snapshot(db, snapshot)
        current = verify_generator_v2_snapshot(
            db, created.snapshot_id, expected_order_id=snapshot.order_id,
            generation_context_hash=snapshot.generation_context_hash,
            constraint_projection_hash=snapshot.constraint_projection_hash,
            site_plan_hash=snapshot.site_plan_hash, planner_operation_key=snapshot.planner_operation_key,
            selected_design=snapshot.selected_design,
        )
        stale = verify_generator_v2_snapshot(
            db, created.snapshot_id, expected_order_id=snapshot.order_id,
            generation_context_hash="f" * 64,
            constraint_projection_hash=snapshot.constraint_projection_hash,
            site_plan_hash=snapshot.site_plan_hash, planner_operation_key=snapshot.planner_operation_key,
            selected_design=snapshot.selected_design,
        )
        wrong_order = get_generator_v2_snapshot(db, created.snapshot_id, expected_order_id="other-order")
        assert current.status == "CURRENT"
        assert stale.status == "STALE" and stale.reason_codes == ("context_hash_mismatch",)
        assert wrong_order.status == "CORRUPT_OR_MISMATCH"
        assert wrong_order.reason_codes == ("snapshot_order_mismatch",)


def test_job_reference_contains_only_immutable_identity(session_maker):
    snapshot = _snapshot()
    with session_maker() as db:
        created = persist_generator_v2_snapshot(db, snapshot)
        payload = build_generator_v2_job_payload(created.snapshot_id, snapshot)
        data = payload.model_dump(mode="json")
        assert data["snapshot_id"] == created.snapshot_id
        assert data["implementation_operation_key"] == created.implementation_operation_key
        assert "canonical_brief" not in data and "brief_answers" not in data


@pytest.mark.parametrize("case_id", REPRESENTATIVE)
def test_seven_representative_snapshots_round_trip(session_maker, case_id):
    snapshot = _snapshot(case_id)
    with session_maker() as db:
        result = persist_generator_v2_snapshot(db, snapshot)
        loaded = get_generator_v2_snapshot(db, result.snapshot_id)
        assert loaded.status == "READY"
        assert loaded.snapshot == snapshot


def test_large_advanced_has_no_snapshot_to_persist():
    context = CASES["LARGE_ADVANCED_BOUNDARY"].context
    projection = build_planner_constraint_projection_v1(context)
    result = build_generator_v2_input_snapshot(
        context=context, projection=projection, site_plan=None,
        planner_operation_key="a" * 64, selected_design=_selected(context),
    )
    assert result.status == "NOT_READY"


def test_privacy_and_no_legacy_fallback(session_maker):
    snapshot = _snapshot()
    serialized = snapshot.model_dump_json()
    for forbidden in ("brief_answers", "extended_brief", "canonical_brief", "email", "phone", "api_key", "payment"):
        assert forbidden not in serialized
    with session_maker() as db:
        result = persist_generator_v2_snapshot(db, snapshot)
        stored = db.get(GeneratorV2Snapshot, result.snapshot_id).snapshot_json
        assert "site_plan" in stored and "brief_answers" not in stored


def test_phase_b_service_has_no_runtime_side_effect_imports():
    source = Path("backend/app/services/generator_v2_snapshot_service.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    assert not any(any(token in name.lower() for token in ("openai", "redis", "worker", "queue", "generation")) for name in imports)
