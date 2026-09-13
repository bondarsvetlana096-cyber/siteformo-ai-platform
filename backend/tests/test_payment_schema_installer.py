from __future__ import annotations

import concurrent.futures
import importlib.util
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.services.db.payment_schema_installer import (
    LOCK_KEY, OWNED_RELATIONS, PaymentSchemaError, SchemaState, TABLES,
    _relations, _table_differences, install_schema, verify_schema,
)


def _local_engine(name):
    url = os.getenv(name)
    if not url:
        pytest.skip(f"{name} is not configured")
    parsed = urlparse(url)
    assert parsed.hostname in {"localhost", "127.0.0.1"} and parsed.scheme.startswith("postgresql")
    return create_engine(url, pool_pre_ping=True)


def _migration(engine, filename, name):
    path = Path(__file__).parents[1] / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    with engine.begin() as connection:
        module.op = Operations(MigrationContext.configure(connection)); module.upgrade()


def _clean(engine):
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE")); connection.execute(text("CREATE SCHEMA public"))


def _pre_payment(engine):
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE orders (id VARCHAR(36) PRIMARY KEY)"))
    _migration(engine, "0007_assistant_core_v1.py", "payment_core")
    _migration(engine, "0008_siteformo_journey_identity_v1.py", "payment_journey")
    _migration(engine, "0009_q1_v2_journey_projects.py", "payment_q1")


@pytest.fixture
def engine():
    value = _local_engine("PAYMENT_INSTALLER_TEST_DATABASE_URL")
    _clean(value); _pre_payment(value); yield value; _clean(value); value.dispose()


def test_install_exact_repeat_noop_and_empty_rows(engine):
    assert verify_schema(engine).state is SchemaState.ABSENT
    exact, changed = install_schema(engine); assert changed and exact.state is SchemaState.EXACT
    exact, changed = install_schema(engine); assert not changed and exact.state is SchemaState.EXACT
    with engine.connect() as connection:
        assert _relations(connection) == OWNED_RELATIONS
        assert all(connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0 for table in TABLES)


def test_concurrent_installers_and_advisory_lock(engine):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: install_schema(engine), range(2)))
    assert sorted(changed for _, changed in results) == [False, True]
    assert all(result.state is SchemaState.EXACT for result, _ in results)
    _clean(engine); _pre_payment(engine)
    with engine.begin() as lock_connection:
        lock_connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_KEY})
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(install_schema, engine); time.sleep(.25); assert not future.done()
        lock_connection.commit()
    result, changed = future.result(timeout=5); pool.shutdown()
    assert changed and result.state is SchemaState.EXACT


def test_partial_and_mismatch_hard_stop_unchanged(engine):
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE orders ADD COLUMN deposit_status VARCHAR(32)"))
    assert verify_schema(engine).state is SchemaState.PARTIAL
    with pytest.raises(PaymentSchemaError, match="Refusing"):
        install_schema(engine)
    assert "deposit_status" in {item["name"] for item in inspect(engine).get_columns("orders")}
    _clean(engine); _pre_payment(engine); install_schema(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE orders ALTER COLUMN deposit_currency TYPE VARCHAR(4)"))
    assert verify_schema(engine).state is SchemaState.MISMATCH
    with pytest.raises(PaymentSchemaError, match="Refusing"):
        install_schema(engine)
    assert str({item["name"]: item["type"] for item in inspect(engine).get_columns("orders")}["deposit_currency"]) == "VARCHAR(4)"


def test_injected_failure_rolls_back_all_objects(engine):
    def fail(_connection):
        raise RuntimeError("injected")
    with pytest.raises(RuntimeError, match="injected"):
        install_schema(engine, failure_hook=fail)
    assert verify_schema(engine).state is SchemaState.ABSENT


def test_installer_catalog_equals_migration_0010(engine):
    other = _local_engine("PAYMENT_MIGRATION_TEST_DATABASE_URL"); _clean(other)
    try:
        _pre_payment(other); _migration(other, "0010_payment_boundary_v2.py", "payment_0010")
        install_schema(engine)
        with engine.connect() as installed, other.connect() as migrated:
            assert _relations(installed) == _relations(migrated) == OWNED_RELATIONS
            for table in TABLES:
                assert _table_differences(installed, table) == ([], True)
                assert _table_differences(migrated, table) == ([], True)
            assert verify_schema(other).state is SchemaState.EXACT
    finally:
        _clean(other); other.dispose()


def test_unique_active_session_and_event_constraints(engine):
    install_schema(engine)
    attempt_sql = text("""
        INSERT INTO payment_attempts (
          id, order_id, siteformo_visitor_id, attempt_type, stripe_checkout_session_id,
          scope_snapshot_hash, scope_version, scope_snapshot, base_package,
          base_package_price_cents, currency, expected_total_amount_cents,
          expected_deposit_amount_cents, confirmed_addons_snapshot,
          legal_terms_version, legal_confirmed_at, status
        ) VALUES (
          :id, 'order-1', '11111111-1111-1111-1111-111111111111', 'initial_deposit', :session,
          :hash, 'payment_scope_v2', '{}'::jsonb, 'starter', 90000, 'EUR', 45000,
          45000, '[]'::jsonb, 'test-v1', now(), :status
        )
    """)
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(attempt_sql, {"id": "missing-order", "session": "cs_missing_order", "hash": "0" * 64, "status": "failed"})
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO orders (id) VALUES ('order-1')"))
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(attempt_sql, {"id": "missing-visitor", "session": "cs_missing_visitor", "hash": "0" * 64, "status": "failed"})
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO siteformo_visitors (id,credential_hash) VALUES ('11111111-1111-1111-1111-111111111111','test-payment-visitor')"))
        connection.execute(attempt_sql, {"id": "attempt-1", "session": "cs_unique", "hash": "a" * 64, "status": "checkout_created"})
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(attempt_sql, {"id": "attempt-2", "session": "cs_other", "hash": "b" * 64, "status": "pending"})
    with engine.begin() as connection:
        connection.execute(text("UPDATE payment_attempts SET status='superseded' WHERE id='attempt-1'"))
        connection.execute(attempt_sql, {"id": "attempt-2", "session": "cs_other", "hash": "b" * 64, "status": "pending"})
        connection.execute(text("INSERT INTO stripe_webhook_events (id,stripe_event_id,event_type) VALUES ('event-1','evt_unique','checkout.session.completed')"))
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO stripe_webhook_events (id,stripe_event_id,event_type) VALUES ('event-2','evt_unique','checkout.session.completed')"))
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(attempt_sql, {"id": "attempt-3", "session": "cs_other", "hash": "c" * 64, "status": "failed"})


def test_installer_is_manual_only_without_alembic_stamp_or_runtime_import():
    backend = Path(__file__).parents[1]
    source = (backend / "app/services/db/payment_schema_installer.py").read_text(encoding="utf-8")
    assert "requires PostgreSQL" in source and "pg_advisory_xact_lock" in source
    assert "alembic_version" not in source
    for relative in ("app/main.py", "app/workers/worker.py", "app/workers/generation_worker.py"):
        assert "payment_schema_installer" not in (backend / relative).read_text(encoding="utf-8")
