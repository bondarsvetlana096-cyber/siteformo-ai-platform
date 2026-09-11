"""Explicit one-off PostgreSQL installer/verifier for the Q1 Journey/Order binding.

Never imported by API or worker startup. Run manually with
``python -m app.services.db.q1_schema_installer verify|install``.
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy import Connection, Engine, inspect, text
from sqlalchemy.dialects import postgresql

from app.db.session import engine as primary_engine


TABLE = "siteformo_journey_projects"
OWNED_RELATIONS = {
    TABLE,
    f"{TABLE}_pkey",
    f"{TABLE}_order_id_key",
    f"ix_{TABLE}_siteformo_visitor_id",
    f"uq_{TABLE}_current_visitor",
}
LOCK_KEY = 0x5346513156320009


class SchemaState(str, Enum):
    ABSENT = "ABSENT"
    EXACT = "EXACT"
    PARTIAL = "PARTIAL"
    MISMATCH = "MISMATCH"


@dataclass(frozen=True)
class VerificationResult:
    state: SchemaState
    differences: tuple[str, ...] = ()


class Q1SchemaError(RuntimeError):
    pass


def _require_postgresql(bind: Engine | Connection) -> None:
    if bind.dialect.name != "postgresql":
        raise Q1SchemaError("Q1 schema installer requires PostgreSQL")


def _norm_type(value: Any) -> str:
    compiled = " ".join(value.compile(dialect=postgresql.dialect()).upper().split())
    return compiled.replace("TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ").replace("CHARACTER VARYING", "VARCHAR")


def _norm_default(value: Any) -> str | None:
    if value is None:
        return None
    return re.sub(r"\s+|::[a-z ]+", "", str(value).lower()).strip("()")


def _norm_where(value: Any) -> str | None:
    if value is None:
        return None
    return re.sub(r"[\s()]|::[a-z]+", "", str(value).lower())


EXPECTED_COLUMNS = {
    "id": ("UUID", False, None),
    "siteformo_visitor_id": ("UUID", False, None),
    "order_id": ("VARCHAR(36)", False, None),
    "handoff_id": ("VARCHAR(128)", True, None),
    "is_current": ("BOOLEAN", False, "true"),
    "created_at": ("TIMESTAMPTZ", False, "now"),
    "updated_at": ("TIMESTAMPTZ", False, "now"),
}


def _relations(connection: Connection) -> set[str]:
    rows = connection.execute(text("""
        SELECT c.relname FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=current_schema() AND c.relname=ANY(:names)
          AND c.relkind IN ('r','p','i','v','m','S')
    """), {"names": sorted(OWNED_RELATIONS)})
    return {row[0] for row in rows}


def _prerequisite_differences(connection: Connection) -> list[str]:
    inspector = inspect(connection); tables = set(inspector.get_table_names()); differences = []
    for table, column, expected_type in (("orders", "id", "VARCHAR(36)"), ("siteformo_visitors", "id", "UUID")):
        if table not in tables:
            differences.append(f"missing prerequisite table: {table}"); continue
        columns = {item["name"]: item for item in inspector.get_columns(table)}
        if column not in columns or _norm_type(columns[column]["type"]) != expected_type or columns[column]["nullable"]:
            differences.append(f"unsupported prerequisite: {table}.{column}")
        if tuple(inspector.get_pk_constraint(table).get("constrained_columns") or ()) != (column,):
            differences.append(f"unsupported prerequisite primary key: {table}.{column}")
    return differences


def _table_differences(connection: Connection) -> tuple[list[str], bool]:
    inspector = inspect(connection); differences = []; missing_only = True
    actual_columns = {item["name"]: item for item in inspector.get_columns(TABLE)}
    missing = sorted(set(EXPECTED_COLUMNS) - set(actual_columns)); extra = sorted(set(actual_columns) - set(EXPECTED_COLUMNS))
    if missing: differences.append("missing columns: " + ", ".join(missing))
    if extra: differences.append("unexpected columns: " + ", ".join(extra)); missing_only = False
    for name in sorted(set(EXPECTED_COLUMNS) & set(actual_columns)):
        item = actual_columns[name]; expected_type, nullable, default = EXPECTED_COLUMNS[name]
        actual = (_norm_type(item["type"]), item["nullable"], _norm_default(item.get("default")))
        if actual != (expected_type, nullable, default): differences.append(f"{name}: definition differs"); missing_only = False
    if tuple(inspector.get_pk_constraint(TABLE).get("constrained_columns") or ()) != ("id",): differences.append("primary key differs"); missing_only = False
    uniques = {tuple(item["column_names"]) for item in inspector.get_unique_constraints(TABLE)}
    if ("order_id",) not in uniques: differences.append("missing order_id unique constraint")
    foreign_keys = {(tuple(item["constrained_columns"]), item["referred_table"], tuple(item["referred_columns"]), (item.get("options",{}).get("ondelete") or "").upper()) for item in inspector.get_foreign_keys(TABLE)}
    expected_fks = {(('siteformo_visitor_id',),'siteformo_visitors',('id',),'CASCADE'), (('order_id',),'orders',('id',),'CASCADE')}
    if foreign_keys != expected_fks: differences.append("foreign keys differ"); missing_only = False
    indexes = {item["name"]:(tuple(item["column_names"]), bool(item["unique"]), _norm_where(item.get("dialect_options",{}).get("postgresql_where"))) for item in inspector.get_indexes(TABLE) if not item.get("duplicates_constraint")}
    expected_indexes = {f"ix_{TABLE}_siteformo_visitor_id":(("siteformo_visitor_id",),False,None), f"uq_{TABLE}_current_visitor":(("siteformo_visitor_id",),True,"is_current")}
    if indexes != expected_indexes: differences.append("indexes differ"); missing_only = False
    return differences, missing_only


def verify_schema(bind: Engine | Connection = primary_engine) -> VerificationResult:
    _require_postgresql(bind)
    if isinstance(bind, Engine):
        with bind.connect() as connection: return _verify(connection)
    return _verify(bind)


def _verify(connection: Connection) -> VerificationResult:
    prerequisite = _prerequisite_differences(connection)
    relations = _relations(connection); present = TABLE in set(inspect(connection).get_table_names())
    if prerequisite: return VerificationResult(SchemaState.MISMATCH, tuple(prerequisite))
    if not present and not relations: return VerificationResult(SchemaState.ABSENT)
    if not present: return VerificationResult(SchemaState.PARTIAL, ("owned relations exist without table",))
    differences, missing_only = _table_differences(connection)
    unexpected = sorted(relations - OWNED_RELATIONS)
    if unexpected: differences.append("unexpected owned relations: " + ", ".join(unexpected)); missing_only = False
    if not differences: return VerificationResult(SchemaState.EXACT)
    return VerificationResult(SchemaState.PARTIAL if missing_only else SchemaState.MISMATCH, tuple(differences))


DDL = ("""CREATE TABLE siteformo_journey_projects (
 id UUID NOT NULL, siteformo_visitor_id UUID NOT NULL, order_id VARCHAR(36) NOT NULL,
 handoff_id VARCHAR(128), is_current BOOLEAN DEFAULT true NOT NULL,
 created_at TIMESTAMPTZ DEFAULT now() NOT NULL, updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
 PRIMARY KEY (id), UNIQUE (order_id),
 FOREIGN KEY(siteformo_visitor_id) REFERENCES siteformo_visitors(id) ON DELETE CASCADE,
 FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
)""",
"CREATE INDEX ix_siteformo_journey_projects_siteformo_visitor_id ON siteformo_journey_projects(siteformo_visitor_id)",
"CREATE UNIQUE INDEX uq_siteformo_journey_projects_current_visitor ON siteformo_journey_projects(siteformo_visitor_id) WHERE is_current")


def install_schema(bind: Engine = primary_engine, *, failure_hook: Any | None = None) -> tuple[VerificationResult, bool]:
    _require_postgresql(bind)
    with bind.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_KEY})
        before = _verify(connection)
        if before.state is SchemaState.EXACT: return before, False
        if before.state is not SchemaState.ABSENT:
            raise Q1SchemaError(f"Refusing Q1 schema installation: {before.state.value}: " + "; ".join(before.differences))
        for statement in DDL:
            connection.execute(text(statement))
        if failure_hook: failure_hook(connection)
        after = _verify(connection)
        if after.state is not SchemaState.EXACT:
            raise Q1SchemaError("Q1 schema post-verification failed: " + "; ".join(after.differences))
        return after, True


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("action", choices=("verify","install")); args=parser.parse_args(argv)
    try:
        result, changed = (verify_schema(), False) if args.action=="verify" else install_schema()
        print(("INSTALLED" if changed else "NO-OP") if args.action=="install" else result.state.value)
        for difference in result.differences: print("-", difference)
        return 0 if result.state in {SchemaState.ABSENT,SchemaState.EXACT} else 2
    except Q1SchemaError as exc: print("ERROR:", exc); return 2
    except Exception: print("ERROR: Q1 schema operation failed"); return 2


if __name__ == "__main__": raise SystemExit(main())
