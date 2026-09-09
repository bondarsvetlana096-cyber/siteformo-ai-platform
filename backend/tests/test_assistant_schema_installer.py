from __future__ import annotations

import concurrent.futures
import importlib.util
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app.services.db.assistant_schema_installer import (
    ASSISTANT_TABLES,
    AssistantSchemaError,
    SchemaState,
    _actual_table,
    install_schema,
    verify_schema,
)


def _local_postgres_engine(variable: str):
    url = os.getenv(variable)
    if not url:
        pytest.skip(f"{variable} is not configured")
    parsed = urlparse(url)
    assert parsed.hostname in {"127.0.0.1", "localhost"}
    assert parsed.scheme.startswith("postgresql")
    return create_engine(url, pool_pre_ping=True)


@pytest.fixture()
def installer_engine():
    engine = _local_postgres_engine("ASSISTANT_INSTALLER_TEST_DATABASE_URL")
    _drop_assistant_schema(engine)
    yield engine
    _drop_assistant_schema(engine)
    engine.dispose()


def _drop_assistant_schema(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS assistant_messages CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS assistant_conversations CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS assistant_visitors CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS siteformo_visitors CASCADE"))


def _run_migration(engine, filename: str, module_name: str) -> None:
    migration_path = Path(__file__).parents[1] / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(module_name, migration_path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()


def _install_core_v1(engine) -> None:
    _run_migration(engine, "0007_assistant_core_v1.py", "assistant_core_v1_fixture")


def test_verify_install_noop_and_exact(installer_engine):
    assert verify_schema(installer_engine).state is SchemaState.ABSENT
    result, created = install_schema(installer_engine)
    assert created is True and result.state is SchemaState.EXACT
    assert verify_schema(installer_engine).state is SchemaState.EXACT
    result, created = install_schema(installer_engine)
    assert created is False and result.state is SchemaState.EXACT


def test_concurrent_install_is_serialized_and_exact(installer_engine):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: install_schema(installer_engine), range(2)))
    assert sorted(created for _, created in outcomes) == [False, True]
    assert all(result.state is SchemaState.EXACT for result, _ in outcomes)
    assert verify_schema(installer_engine).state is SchemaState.EXACT


def test_partial_schema_is_reported_and_never_repaired(installer_engine):
    with installer_engine.begin() as connection:
        connection.execute(text("CREATE TABLE assistant_visitors (id UUID PRIMARY KEY)"))
    result = verify_schema(installer_engine)
    assert result.state is SchemaState.MISMATCH
    assert any("missing tables" in item for item in result.differences)
    with pytest.raises(AssistantSchemaError, match="Refusing"):
        install_schema(installer_engine)
    assert set(inspect(installer_engine).get_table_names()).intersection(ASSISTANT_TABLES) == {
        "assistant_visitors"
    }


def test_partial_but_correct_object_is_reported_partial(installer_engine):
    from app.journey.models import SiteFormoVisitor

    SiteFormoVisitor.__table__.create(installer_engine)
    result = verify_schema(installer_engine)
    assert result.state is SchemaState.PARTIAL
    with pytest.raises(AssistantSchemaError, match="PARTIAL"):
        install_schema(installer_engine)


def test_semantic_mismatch_is_reported_and_never_repaired(installer_engine):
    with installer_engine.begin() as connection:
        connection.execute(text("CREATE TABLE assistant_visitors (id UUID PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE assistant_conversations (id UUID PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE assistant_messages (id UUID PRIMARY KEY)"))
    result = verify_schema(installer_engine)
    assert result.state is SchemaState.MISMATCH
    with pytest.raises(AssistantSchemaError, match="MISMATCH"):
        install_schema(installer_engine)
    with installer_engine.connect() as connection:
        assert len(inspect(connection).get_columns("assistant_visitors")) == 1


def test_failure_rolls_back_all_ddl(installer_engine):
    def fail(_connection):
        raise RuntimeError("injected failure")

    with pytest.raises(RuntimeError, match="injected failure"):
        install_schema(installer_engine, failure_hook=fail)
    assert verify_schema(installer_engine).state is SchemaState.ABSENT


def test_installer_catalog_matches_migration_0007(installer_engine):
    migration_engine = _local_postgres_engine("ASSISTANT_MIGRATION_TEST_DATABASE_URL")
    _drop_assistant_schema(migration_engine)
    try:
        install_schema(installer_engine)
        _run_migration(migration_engine, "0007_assistant_core_v1.py", "assistant_migration_equivalence")
        _run_migration(
            migration_engine,
            "0008_siteformo_journey_identity_v1.py",
            "journey_migration_equivalence",
        )

        assert verify_schema(migration_engine).state is SchemaState.EXACT
        with installer_engine.connect() as installer_connection, migration_engine.connect() as migration_connection:
            assert {
                table: _actual_table(installer_connection, table) for table in ASSISTANT_TABLES
            } == {
                table: _actual_table(migration_connection, table) for table in ASSISTANT_TABLES
            }
    finally:
        _drop_assistant_schema(migration_engine)
        migration_engine.dispose()


def test_installer_upgrades_current_production_style_core_v1(installer_engine):
    _install_core_v1(installer_engine)
    before = verify_schema(installer_engine)
    assert before.state is SchemaState.PARTIAL
    assert before.differences == (
        "upgradeable Assistant Core V1 schema present; Journey Identity V1 absent",
    )
    result, changed = install_schema(installer_engine)
    assert changed is True and result.state is SchemaState.EXACT
    result, changed = install_schema(installer_engine)
    assert changed is False and result.state is SchemaState.EXACT


def test_core_v1_upgrade_preserves_existing_assistant_identity(installer_engine):
    _install_core_v1(installer_engine)
    with installer_engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO assistant_visitors (id, credential_hash)
            VALUES ('10000000-0000-0000-0000-000000000001', :credential_hash)
        """), {"credential_hash": "a" * 64})
    install_schema(installer_engine)
    with installer_engine.connect() as connection:
        row = connection.execute(text("""
            SELECT av.id, av.siteformo_visitor_id, av.credential_hash, sv.credential_hash
            FROM assistant_visitors av
            JOIN siteformo_visitors sv ON sv.id = av.siteformo_visitor_id
        """)).one()
        assert row.id == row.siteformo_visitor_id
        assert row.credential_hash == row[3] == "a" * 64


def test_concurrent_core_v1_upgrade_is_serialized(installer_engine):
    _install_core_v1(installer_engine)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: install_schema(installer_engine), range(2)))
    assert sorted(changed for _, changed in outcomes) == [False, True]
    assert verify_schema(installer_engine).state is SchemaState.EXACT


def test_core_v1_upgrade_failure_rolls_back(installer_engine):
    _install_core_v1(installer_engine)

    def fail(_connection):
        raise RuntimeError("injected journey upgrade failure")

    with pytest.raises(RuntimeError, match="injected journey upgrade failure"):
        install_schema(installer_engine, failure_hook=fail)
    assert verify_schema(installer_engine).state is SchemaState.PARTIAL
    assert "siteformo_visitors" not in inspect(installer_engine).get_table_names()


def test_installer_is_not_wired_into_api_or_worker_startup():
    backend = Path(__file__).parents[1]
    forbidden = (
        backend / "app" / "main.py",
        backend / "app" / "services" / "db" / "init_db.py",
        backend / "app" / "services" / "db" / "migrations.py",
        backend / "app" / "workers" / "worker.py",
        backend / "app" / "workers" / "generation_worker.py",
    )
    for path in forbidden:
        source = path.read_text(encoding="utf-8")
        assert "assistant_schema_installer" not in source
        assert "install_schema(" not in source
