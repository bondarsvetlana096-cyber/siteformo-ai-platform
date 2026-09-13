"""Manual PostgreSQL-only Payment Boundary V2 installer/verifier.

Not imported by API or worker startup. Invoke explicitly with
``python -m app.services.db.payment_schema_installer verify|install``.
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

LOCK_KEY = 0x5346504159560010
TABLES = {"payment_attempts", "stripe_webhook_events"}
ORDER_COLUMNS = {
    "deposit_status", "deposit_paid_at", "deposit_amount_cents", "deposit_currency",
    "brief_confirmed_at", "legal_terms_version", "legal_confirmed_at",
    "prepayment_summary_email_status", "prepayment_summary_email_sent_at",
}
EXPECTED_ORDER_COLUMNS = {
    "deposit_status": ("VARCHAR(32)", False, "not_started"),
    "deposit_paid_at": ("TIMESTAMPTZ", True, None),
    "deposit_amount_cents": ("INTEGER", True, None),
    "deposit_currency": ("VARCHAR(3)", True, None),
    "brief_confirmed_at": ("TIMESTAMPTZ", True, None),
    "legal_terms_version": ("VARCHAR(128)", True, None),
    "legal_confirmed_at": ("TIMESTAMPTZ", True, None),
    "prepayment_summary_email_status": ("VARCHAR(16)", False, "pending"),
    "prepayment_summary_email_sent_at": ("TIMESTAMPTZ", True, None),
}
OWNED_RELATIONS = TABLES | {
    "payment_attempts_pkey", "payment_attempts_stripe_checkout_session_id_key",
    "ix_payment_attempts_order_id", "ix_payment_attempts_siteformo_visitor_id",
    "ix_payment_attempts_status", "uq_payment_attempts_active_order",
    "stripe_webhook_events_pkey", "stripe_webhook_events_stripe_event_id_key",
}


class SchemaState(str, Enum):
    ABSENT = "ABSENT"
    EXACT = "EXACT"
    PARTIAL = "PARTIAL"
    MISMATCH = "MISMATCH"


@dataclass(frozen=True)
class VerificationResult:
    state: SchemaState
    differences: tuple[str, ...] = ()


class PaymentSchemaError(RuntimeError):
    pass


def _require_postgresql(bind: Engine | Connection) -> None:
    if bind.dialect.name != "postgresql":
        raise PaymentSchemaError("Payment schema installer requires PostgreSQL")


def _norm_type(value: Any) -> str:
    compiled = " ".join(value.compile(dialect=postgresql.dialect()).upper().split())
    return compiled.replace("TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ").replace(
        "CHARACTER VARYING", "VARCHAR"
    )


def _norm_default(value: Any) -> str | None:
    if value is None:
        return None
    return re.sub(r"\s+|::[a-z ]+", "", str(value).lower()).strip("()'")


def _norm_where(value: Any) -> str | None:
    if value is None:
        return None
    return re.sub(r"[\s()]|::[a-z]+|'", "", str(value).lower())


EXPECTED_COLUMNS = {
    "payment_attempts": {
        "id": ("VARCHAR(36)", False, None), "order_id": ("VARCHAR(36)", False, None),
        "siteformo_visitor_id": ("UUID", False, None), "attempt_type": ("VARCHAR(32)", False, None),
        "stripe_checkout_session_id": ("VARCHAR(255)", True, None), "stripe_payment_intent_id": ("VARCHAR(255)", True, None),
        "checkout_url": ("TEXT", True, None), "scope_snapshot_hash": ("VARCHAR(64)", False, None),
        "scope_version": ("VARCHAR(64)", False, None), "scope_snapshot": ("JSONB", False, None),
        "base_package": ("VARCHAR(32)", False, None), "base_package_price_cents": ("INTEGER", False, None),
        "currency": ("VARCHAR(3)", False, None), "expected_total_amount_cents": ("INTEGER", False, None),
        "expected_deposit_amount_cents": ("INTEGER", False, None), "confirmed_addons_snapshot": ("JSONB", False, None),
        "legal_terms_version": ("VARCHAR(128)", False, None), "legal_confirmed_at": ("TIMESTAMPTZ", False, None),
        "status": ("VARCHAR(32)", False, None), "created_at": ("TIMESTAMPTZ", False, "now"),
        "expires_at": ("TIMESTAMPTZ", True, None), "completed_at": ("TIMESTAMPTZ", True, None),
        "superseded_at": ("TIMESTAMPTZ", True, None),
    },
    "stripe_webhook_events": {
        "id": ("VARCHAR(36)", False, None), "stripe_event_id": ("VARCHAR(255)", False, None),
        "event_type": ("VARCHAR(128)", False, None), "received_at": ("TIMESTAMPTZ", False, "now"),
        "processed_at": ("TIMESTAMPTZ", True, None), "processing_result": ("VARCHAR(64)", True, None),
        "payment_attempt_id": ("VARCHAR(36)", True, None), "order_id": ("VARCHAR(36)", True, None),
    },
}


def _relations(connection: Connection) -> set[str]:
    rows = connection.execute(text("""
        SELECT c.relname FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=current_schema() AND c.relname=ANY(:names)
          AND c.relkind IN ('r','p','i','v','m','S')
    """), {"names": sorted(OWNED_RELATIONS)})
    return {row[0] for row in rows}


def _prerequisites(connection: Connection) -> list[str]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    missing = [name for name in ("orders", "siteformo_visitors", "siteformo_journey_projects") if name not in tables]
    differences = [f"missing prerequisite table: {name}" for name in missing]
    if "orders" in tables:
        columns = {item["name"]: item for item in inspector.get_columns("orders")}
        if "id" not in columns or _norm_type(columns["id"]["type"]) != "VARCHAR(36)":
            differences.append("unsupported prerequisite: orders.id")
    return differences


def _table_differences(connection: Connection, table: str) -> tuple[list[str], bool]:
    inspector = inspect(connection)
    actual = {item["name"]: item for item in inspector.get_columns(table)}
    expected = EXPECTED_COLUMNS[table]
    differences: list[str] = []
    missing_only = True
    missing, extra = sorted(set(expected) - set(actual)), sorted(set(actual) - set(expected))
    if missing:
        differences.append(f"{table}: missing columns: " + ", ".join(missing))
    if extra:
        differences.append(f"{table}: unexpected columns: " + ", ".join(extra)); missing_only = False
    for name in sorted(set(expected) & set(actual)):
        item = actual[name]
        observed = (_norm_type(item["type"]), item["nullable"], _norm_default(item.get("default")))
        if observed != expected[name]:
            differences.append(f"{table}.{name}: definition differs"); missing_only = False
    if tuple(inspector.get_pk_constraint(table).get("constrained_columns") or ()) != ("id",):
        differences.append(f"{table}: primary key differs"); missing_only = False
    uniques = {tuple(item["column_names"]) for item in inspector.get_unique_constraints(table)}
    required = ("stripe_checkout_session_id",) if table == "payment_attempts" else ("stripe_event_id",)
    if required not in uniques:
        differences.append(f"{table}: unique constraint missing")
    foreign_keys = {
        (tuple(item["constrained_columns"]), item["referred_table"],
         tuple(item["referred_columns"]), (item.get("options", {}).get("ondelete") or "").upper())
        for item in inspector.get_foreign_keys(table)
    }
    expected_foreign_keys = (
        {(('order_id',), 'orders', ('id',), 'CASCADE'),
         (('siteformo_visitor_id',), 'siteformo_visitors', ('id',), 'CASCADE')}
        if table == "payment_attempts"
        else {(('payment_attempt_id',), 'payment_attempts', ('id',), 'SET NULL')}
    )
    if foreign_keys != expected_foreign_keys:
        differences.append(f"{table}: foreign keys differ"); missing_only = False
    return differences, missing_only


def _verify(connection: Connection) -> VerificationResult:
    prerequisite = _prerequisites(connection)
    if prerequisite:
        return VerificationResult(SchemaState.MISMATCH, tuple(prerequisite))
    inspector = inspect(connection)
    tables, relations = set(inspector.get_table_names()), _relations(connection)
    present_tables = TABLES & tables
    order_columns = {item["name"] for item in inspector.get_columns("orders")}
    present_order_columns = ORDER_COLUMNS & order_columns
    if not present_tables and not relations and not present_order_columns:
        return VerificationResult(SchemaState.ABSENT)
    if present_tables != TABLES or present_order_columns != ORDER_COLUMNS:
        return VerificationResult(SchemaState.PARTIAL, ("payment-owned objects are only partially present",))
    differences: list[str] = []
    missing_only = True
    order_definitions = {item["name"]: item for item in inspector.get_columns("orders")}
    for name, expected in EXPECTED_ORDER_COLUMNS.items():
        item = order_definitions[name]
        observed = (_norm_type(item["type"]), item["nullable"], _norm_default(item.get("default")))
        if observed != expected:
            differences.append(f"orders.{name}: definition differs"); missing_only = False
    for table in sorted(TABLES):
        found, only_missing = _table_differences(connection, table)
        differences.extend(found); missing_only = missing_only and only_missing
    indexes = {
        item["name"]: (tuple(item["column_names"]), bool(item["unique"]), _norm_where(item.get("dialect_options", {}).get("postgresql_where")))
        for item in inspector.get_indexes("payment_attempts") if not item.get("duplicates_constraint")
    }
    expected_indexes = {
        "ix_payment_attempts_order_id": (("order_id",), False, None),
        "ix_payment_attempts_siteformo_visitor_id": (("siteformo_visitor_id",), False, None),
        "ix_payment_attempts_status": (("status",), False, None),
        "uq_payment_attempts_active_order": (
            ("order_id",), True,
            "status=anyarray[creatingvarying,checkout_createdvarying,pendingvarying][]",
        ),
    }
    if indexes != expected_indexes:
        differences.append("payment_attempts: indexes differ"); missing_only = False
    if relations != OWNED_RELATIONS:
        differences.append("payment-owned relation set differs"); missing_only = False
    if not differences:
        return VerificationResult(SchemaState.EXACT)
    return VerificationResult(SchemaState.PARTIAL if missing_only else SchemaState.MISMATCH, tuple(differences))


def verify_schema(bind: Engine | Connection = primary_engine) -> VerificationResult:
    _require_postgresql(bind)
    if isinstance(bind, Engine):
        with bind.connect() as connection:
            return _verify(connection)
    return _verify(bind)


DDL = (
    "ALTER TABLE orders ADD COLUMN deposit_status VARCHAR(32) DEFAULT 'not_started' NOT NULL",
    "ALTER TABLE orders ADD COLUMN deposit_paid_at TIMESTAMPTZ", "ALTER TABLE orders ADD COLUMN deposit_amount_cents INTEGER",
    "ALTER TABLE orders ADD COLUMN deposit_currency VARCHAR(3)", "ALTER TABLE orders ADD COLUMN brief_confirmed_at TIMESTAMPTZ",
    "ALTER TABLE orders ADD COLUMN legal_terms_version VARCHAR(128)", "ALTER TABLE orders ADD COLUMN legal_confirmed_at TIMESTAMPTZ",
    "ALTER TABLE orders ADD COLUMN prepayment_summary_email_status VARCHAR(16) DEFAULT 'pending' NOT NULL",
    "ALTER TABLE orders ADD COLUMN prepayment_summary_email_sent_at TIMESTAMPTZ",
    """CREATE TABLE payment_attempts (
      id VARCHAR(36) PRIMARY KEY, order_id VARCHAR(36) NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
      siteformo_visitor_id UUID NOT NULL REFERENCES siteformo_visitors(id) ON DELETE CASCADE,
      attempt_type VARCHAR(32) NOT NULL,
      stripe_checkout_session_id VARCHAR(255) UNIQUE, stripe_payment_intent_id VARCHAR(255), checkout_url TEXT,
      scope_snapshot_hash VARCHAR(64) NOT NULL, scope_version VARCHAR(64) NOT NULL, scope_snapshot JSONB NOT NULL,
      base_package VARCHAR(32) NOT NULL, base_package_price_cents INTEGER NOT NULL, currency VARCHAR(3) NOT NULL,
      expected_total_amount_cents INTEGER NOT NULL, expected_deposit_amount_cents INTEGER NOT NULL,
      confirmed_addons_snapshot JSONB NOT NULL, legal_terms_version VARCHAR(128) NOT NULL,
      legal_confirmed_at TIMESTAMPTZ NOT NULL, status VARCHAR(32) NOT NULL,
      created_at TIMESTAMPTZ DEFAULT now() NOT NULL, expires_at TIMESTAMPTZ,
      completed_at TIMESTAMPTZ, superseded_at TIMESTAMPTZ)""",
    "CREATE INDEX ix_payment_attempts_order_id ON payment_attempts(order_id)",
    "CREATE INDEX ix_payment_attempts_siteformo_visitor_id ON payment_attempts(siteformo_visitor_id)",
    "CREATE INDEX ix_payment_attempts_status ON payment_attempts(status)",
    "CREATE UNIQUE INDEX uq_payment_attempts_active_order ON payment_attempts(order_id) WHERE status IN ('creating','checkout_created','pending')",
    """CREATE TABLE stripe_webhook_events (
      id VARCHAR(36) PRIMARY KEY, stripe_event_id VARCHAR(255) NOT NULL UNIQUE,
      event_type VARCHAR(128) NOT NULL, received_at TIMESTAMPTZ DEFAULT now() NOT NULL,
      processed_at TIMESTAMPTZ, processing_result VARCHAR(64),
      payment_attempt_id VARCHAR(36) REFERENCES payment_attempts(id) ON DELETE SET NULL, order_id VARCHAR(36))""",
)


def install_schema(bind: Engine = primary_engine, *, failure_hook: Any | None = None) -> tuple[VerificationResult, bool]:
    _require_postgresql(bind)
    with bind.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_KEY})
        before = _verify(connection)
        if before.state is SchemaState.EXACT:
            return before, False
        if before.state is not SchemaState.ABSENT:
            raise PaymentSchemaError(f"Refusing Payment schema installation: {before.state.value}: " + "; ".join(before.differences))
        for statement in DDL:
            connection.execute(text(statement))
        if failure_hook:
            failure_hook(connection)
        after = _verify(connection)
        if after.state is not SchemaState.EXACT:
            raise PaymentSchemaError("Payment schema post-verification failed: " + "; ".join(after.differences))
        return after, True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("action", choices=("verify", "install")); args = parser.parse_args(argv)
    try:
        result, changed = (verify_schema(), False) if args.action == "verify" else install_schema()
        print(("INSTALLED" if changed else "NO-OP") if args.action == "install" else result.state.value)
        for difference in result.differences:
            print("-", difference)
        return 0 if result.state in {SchemaState.ABSENT, SchemaState.EXACT} else 2
    except PaymentSchemaError as exc:
        print("ERROR:", exc); return 2
    except Exception:
        print("ERROR: Payment schema operation failed"); return 2


if __name__ == "__main__":
    raise SystemExit(main())
