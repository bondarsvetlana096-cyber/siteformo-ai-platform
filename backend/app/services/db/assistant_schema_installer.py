"""Explicit, one-off PostgreSQL schema installer for Assistant Core V1.

This module is deliberately not imported by application or worker startup.
Run it explicitly with ``python -m app.services.db.assistant_schema_installer``.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy import Connection, Engine, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine.interfaces import ReflectedIndex
from sqlalchemy.schema import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

from app.assistant.models import AssistantBase
from app.db.session import engine as primary_engine


ASSISTANT_TABLES = (
    "assistant_visitors",
    "assistant_conversations",
    "assistant_messages",
)
ASSISTANT_INDEXES = frozenset(
    index.name
    for table in AssistantBase.metadata.sorted_tables
    for index in table.indexes
    if index.name is not None
)


def _constraint_index_names() -> frozenset[str]:
    names: set[str] = set()
    for table in AssistantBase.metadata.sorted_tables:
        names.add(f"{table.name}_pkey")
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint):
                names.add(constraint.name or f"{table.name}_{'_'.join(constraint.columns.keys())}_key")
    return frozenset(names)


ASSISTANT_CONSTRAINT_INDEXES = _constraint_index_names()
# Stable signed 64-bit key reserved for SiteFormo Assistant schema installation.
ASSISTANT_SCHEMA_ADVISORY_LOCK_KEY = 0x5346415353545631


class SchemaState(str, Enum):
    ABSENT = "ABSENT"
    EXACT = "EXACT"
    PARTIAL = "PARTIAL"
    MISMATCH = "MISMATCH"


@dataclass(frozen=True)
class VerificationResult:
    state: SchemaState
    differences: tuple[str, ...] = ()


class AssistantSchemaError(RuntimeError):
    """Raised when installation cannot safely produce the canonical schema."""


def _require_postgresql(bind: Engine | Connection) -> None:
    if bind.dialect.name != "postgresql":
        raise AssistantSchemaError("Assistant schema installer requires PostgreSQL")


def _compiled_type(column_type: Any) -> str:
    return " ".join(column_type.compile(dialect=postgresql.dialect()).upper().split())


def _normalized_default(value: Any) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"\s+", "", str(value).lower())
    normalized = normalized.replace("::timestampwithtimezone", "")
    return normalized


def _normalized_check(expression: str, column_names: set[str]) -> tuple[tuple[str, ...], frozenset[str]]:
    lowered = expression.lower()
    referenced = tuple(sorted(name for name in column_names if re.search(rf"\b{re.escape(name)}\b", lowered)))
    return referenced, frozenset(re.findall(r"'([^']+)'", expression))


def _index_where(index: Index | ReflectedIndex | dict[str, Any]) -> str | None:
    if isinstance(index, Index):
        clause = index.dialect_options["postgresql"].get("where")
    else:
        clause = index.get("dialect_options", {}).get("postgresql_where")
    if clause is None:
        return None
    return re.sub(r"[\s()]+", "", str(clause).lower()).replace("::text", "")


def _expected_table(table_name: str) -> dict[str, Any]:
    table = AssistantBase.metadata.tables[table_name]
    columns = {
        column.name: {
            "type": _compiled_type(column.type),
            "nullable": column.nullable,
            "default": _normalized_default(column.server_default.arg if column.server_default else None),
        }
        for column in table.columns
    }
    unique_columns = {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    foreign_keys = {
        (
            tuple(constraint.columns.keys()),
            tuple(element.target_fullname.split(".")[0] for element in constraint.elements),
            tuple(element.target_fullname.split(".")[1] for element in constraint.elements),
            (constraint.ondelete or "").upper(),
        )
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    checks = {
        constraint.name: _normalized_check(str(constraint.sqltext), set(columns))
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name
    }
    indexes = {
        index.name: {
            "columns": tuple(column.name for column in index.columns),
            "unique": bool(index.unique),
            "where": _index_where(index),
        }
        for index in table.indexes
        if index.name
    }
    return {
        "columns": columns,
        "pk": tuple(table.primary_key.columns.keys()),
        "unique": unique_columns,
        "foreign_keys": foreign_keys,
        "checks": checks,
        "indexes": indexes,
    }


def _actual_table(connection: Connection, table_name: str) -> dict[str, Any]:
    inspector = inspect(connection)
    columns = {
        column["name"]: {
            "type": _compiled_type(column["type"]),
            "nullable": column["nullable"],
            "default": _normalized_default(column.get("default")),
        }
        for column in inspector.get_columns(table_name)
    }
    unique_columns = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints(table_name)
    }
    foreign_keys = {
        (
            tuple(constraint["constrained_columns"]),
            tuple([constraint["referred_table"]] * len(constraint["referred_columns"])),
            tuple(constraint["referred_columns"]),
            (constraint.get("options", {}).get("ondelete") or "").upper(),
        )
        for constraint in inspector.get_foreign_keys(table_name)
    }
    checks = {
        constraint["name"]: _normalized_check(constraint["sqltext"], set(columns))
        for constraint in inspector.get_check_constraints(table_name)
        if constraint.get("name")
    }
    indexes = {
        index["name"]: {
            "columns": tuple(index["column_names"]),
            "unique": bool(index["unique"]),
            "where": _index_where(index),
        }
        for index in inspector.get_indexes(table_name)
        if index.get("name") and not index.get("duplicates_constraint")
    }
    return {
        "columns": columns,
        "pk": tuple(inspector.get_pk_constraint(table_name)["constrained_columns"]),
        "unique": unique_columns,
        "foreign_keys": foreign_keys,
        "checks": checks,
        "indexes": indexes,
    }


def _assistant_relations(connection: Connection) -> set[str]:
    rows = connection.execute(
        text(
            """
            SELECT c.relname
            FROM pg_catalog.pg_class AS c
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema()
              AND (
                c.relname LIKE 'assistant_%'
                OR c.relname LIKE 'ix_assistant_%'
                OR c.relname LIKE 'uq_assistant_%'
                OR c.relname = ANY(:index_names)
              )
              AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'i')
            """
        ),
        {"index_names": list(ASSISTANT_INDEXES)},
    )
    return {row[0] for row in rows}


def verify_schema(bind: Engine | Connection = primary_engine) -> VerificationResult:
    """Inspect Assistant-owned catalog objects without locks, DDL, or writes."""
    _require_postgresql(bind)
    if isinstance(bind, Engine):
        with bind.connect() as connection:
            return _verify_connection(connection)
    return _verify_connection(bind)


def _verify_connection(connection: Connection) -> VerificationResult:
    relations = _assistant_relations(connection)
    present_tables = set(inspect(connection).get_table_names()).intersection(ASSISTANT_TABLES)
    expected_tables = set(ASSISTANT_TABLES)

    if not present_tables and not relations:
        return VerificationResult(SchemaState.ABSENT)

    differences: list[str] = []
    if present_tables != expected_tables:
        missing = sorted(expected_tables - present_tables)
        if missing:
            differences.append("missing tables: " + ", ".join(missing))

    expected_relations = expected_tables | set(ASSISTANT_INDEXES) | set(ASSISTANT_CONSTRAINT_INDEXES)
    unexpected = sorted(relations - expected_relations)
    if unexpected:
        differences.append("unexpected Assistant-owned relations: " + ", ".join(unexpected))

    for table_name in sorted(present_tables):
        expected = _expected_table(table_name)
        actual = _actual_table(connection, table_name)
        for section in ("columns", "pk", "unique", "foreign_keys", "checks", "indexes"):
            if actual[section] != expected[section]:
                differences.append(f"{table_name}: {section} differ")

    if differences:
        only_missing_tables = all(item.startswith("missing tables:") for item in differences)
        state = SchemaState.PARTIAL if only_missing_tables else SchemaState.MISMATCH
        return VerificationResult(state, tuple(differences))
    return VerificationResult(SchemaState.EXACT)


def install_schema(
    bind: Engine = primary_engine,
    *,
    failure_hook: Any | None = None,
) -> tuple[VerificationResult, bool]:
    """Install canonical Assistant metadata atomically, or return an EXACT no-op."""
    _require_postgresql(bind)
    with bind.begin() as connection:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": ASSISTANT_SCHEMA_ADVISORY_LOCK_KEY},
        )
        before = _verify_connection(connection)
        if before.state is SchemaState.EXACT:
            return before, False
        if before.state is not SchemaState.ABSENT:
            raise AssistantSchemaError(
                f"Refusing Assistant schema installation: {before.state.value}: "
                + "; ".join(before.differences)
            )

        AssistantBase.metadata.create_all(bind=connection, checkfirst=False)
        if failure_hook is not None:
            failure_hook(connection)
        after = _verify_connection(connection)
        if after.state is not SchemaState.EXACT:
            raise AssistantSchemaError(
                "Assistant schema post-verification failed: " + "; ".join(after.differences)
            )
        return after, True


def _print_result(result: VerificationResult) -> None:
    print(result.state.value)
    for difference in result.differences:
        print(f"- {difference}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify or install the Assistant Core V1 PostgreSQL schema")
    parser.add_argument("action", choices=("verify", "install"))
    args = parser.parse_args(argv)

    try:
        if args.action == "verify":
            result = verify_schema()
            _print_result(result)
            return 0 if result.state in {SchemaState.ABSENT, SchemaState.EXACT} else 2

        result, created = install_schema()
        print("INSTALLED" if created else "NO-OP")
        _print_result(result)
        return 0
    except AssistantSchemaError as exc:
        print(f"ERROR: {exc}")
        return 2
    except Exception:
        print("ERROR: Assistant schema operation failed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
