from __future__ import annotations

import importlib.util
import concurrent.futures
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app.services.db.q1_schema_installer import LOCK_KEY, OWNED_RELATIONS, Q1SchemaError, SchemaState, TABLE, _relations, _table_differences, install_schema, verify_schema


def local_engine(name):
    url=os.getenv(name)
    if not url: pytest.skip(f"{name} is not configured")
    parsed=urlparse(url); assert parsed.hostname in {"localhost","127.0.0.1"} and parsed.scheme.startswith("postgresql")
    return create_engine(url,pool_pre_ping=True)


def migration(engine, filename, name):
    path=Path(__file__).parents[1]/"alembic"/"versions"/filename
    spec=importlib.util.spec_from_file_location(name,path); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    with engine.begin() as connection:
        module.op=Operations(MigrationContext.configure(connection)); module.upgrade()


def clean(engine):
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE")); c.execute(text("CREATE SCHEMA public"))


def pre_q1(engine):
    with engine.begin() as c: c.execute(text("CREATE TABLE orders (id VARCHAR(36) PRIMARY KEY)"))
    migration(engine,"0007_assistant_core_v1.py","q1_core"); migration(engine,"0008_siteformo_journey_identity_v1.py","q1_journey")


@pytest.fixture()
def engine():
    value=local_engine("Q1_INSTALLER_TEST_DATABASE_URL"); clean(value); pre_q1(value); yield value; clean(value); value.dispose()


def test_verify_install_noop_and_empty_exact_catalog(engine):
    assert verify_schema(engine).state is SchemaState.ABSENT
    exact,changed=install_schema(engine); assert changed and exact.state is SchemaState.EXACT
    exact,changed=install_schema(engine); assert not changed and exact.state is SchemaState.EXACT
    with engine.connect() as c:
        assert _relations(c)==OWNED_RELATIONS
        assert c.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar_one()==0


def test_concurrent_install_and_advisory_lock_serialize(engine):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:install_schema(engine),range(2)))
    assert sorted(changed for _,changed in results)==[False,True]
    assert all(result.state is SchemaState.EXACT for result,_ in results)
    clean(engine); pre_q1(engine)
    with engine.begin() as lock_connection:
        lock_connection.execute(text("SELECT pg_advisory_xact_lock(:key)"),{"key":LOCK_KEY})
        pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future=pool.submit(install_schema,engine); time.sleep(.25); assert not future.done()
        lock_connection.commit()
    result,changed=future.result(timeout=5); pool.shutdown(); assert changed and result.state is SchemaState.EXACT


def test_arbitrary_partial_hard_stops_unchanged(engine):
    with engine.begin() as c: c.execute(text(f"CREATE SEQUENCE {TABLE}"))
    before=verify_schema(engine); assert before.state is SchemaState.PARTIAL
    with pytest.raises(Q1SchemaError,match="Refusing"): install_schema(engine)
    with engine.connect() as c: assert _relations(c)=={TABLE}


def test_mismatch_hard_stops_unchanged(engine):
    with engine.begin() as c: c.execute(text(f"CREATE TABLE {TABLE} (id TEXT PRIMARY KEY)"))
    assert verify_schema(engine).state is SchemaState.MISMATCH
    with pytest.raises(Q1SchemaError,match="Refusing"): install_schema(engine)
    with engine.connect() as c:
        columns=inspect(c).get_columns(TABLE); assert [(x["name"],str(x["type"])) for x in columns]==[("id","TEXT")]


def test_injected_failure_rolls_back_completely(engine):
    def fail(_): raise RuntimeError("injected")
    with pytest.raises(RuntimeError,match="injected"): install_schema(engine,failure_hook=fail)
    assert verify_schema(engine).state is SchemaState.ABSENT


def test_installer_equals_repository_migrations_through_0009(engine):
    other=local_engine("Q1_MIGRATION_TEST_DATABASE_URL"); clean(other)
    try:
        pre_q1(other); migration(other,"0009_q1_v2_journey_projects.py","q1_0009")
        install_schema(engine)
        with engine.connect() as installed, other.connect() as migrated:
            assert _table_differences(installed)==([],True)
            assert _table_differences(migrated)==([],True)
            assert {i["name"] for i in inspect(installed).get_indexes(TABLE)}=={i["name"] for i in inspect(migrated).get_indexes(TABLE)}
    finally: clean(other); other.dispose()


def test_installer_is_manual_postgresql_only_and_has_no_runtime_imports():
    backend=Path(__file__).parents[1]; installer=(backend/"app/services/db/q1_schema_installer.py").read_text()
    assert "requires PostgreSQL" in installer and "pg_advisory_xact_lock" in installer
    for relative in ("app/main.py","app/workers/worker.py","app/workers/generation_worker.py"):
        assert "q1_schema_installer" not in (backend/relative).read_text(encoding="utf-8")
    assistant_installer=(backend/"app/services/db/assistant_schema_installer.py").read_text(encoding="utf-8")
    assert "LIKE 'siteformo_%'" not in assistant_installer
