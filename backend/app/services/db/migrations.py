from sqlalchemy import text

from app.services.db.postgres import engine


def run_lightweight_migrations() -> None:
    if engine is None:
        return

    statements = [
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS status VARCHAR(50) DEFAULT 'new'",
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS contact_channel VARCHAR(50)",
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS is_hot BOOLEAN DEFAULT false",
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS followup_stage INTEGER DEFAULT 0",
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS last_contacted TIMESTAMPTZ",
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS history JSONB DEFAULT '[]'::jsonb",
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS estimate JSONB",
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS offer_url VARCHAR(500)",
    ]

    with engine.begin() as conn:
        for statement in statements:
            try:
                conn.execute(text(statement))
            except Exception as exc:
                print(f"[DB WARNING] lightweight migration failed for {statement!r}: {exc}")



def run_order_logic_migrations(app_engine) -> None:
    """Add current SiteFormo production/review columns to existing orders tables.

    SQLAlchemy create_all creates the columns for new databases, but existing
    Railway/Postgres tables need safe additive migrations.
    """
    if app_engine is None:
        return

    from sqlalchemy import inspect

    try:
        inspector = inspect(app_engine)
        existing = {col["name"] for col in inspector.get_columns("orders")}
    except Exception as exc:
        print(f"[DB WARNING] could not inspect orders table: {exc}")
        return

    dialect = app_engine.dialect.name
    json_type = "JSONB" if dialect == "postgresql" else "JSON"
    dt_type = "TIMESTAMPTZ" if dialect == "postgresql" else "DATETIME"

    columns = {
        "selected_example_id": "VARCHAR(128)",
        "viewed_examples": json_type,
        "example_tracking_payload": json_type,
        "entry_source": "VARCHAR(128)",
        "design_direction": "VARCHAR(128)",
        "interaction_style": "VARCHAR(64)",
        "production_payload": json_type,
        "protected_preview_url": "TEXT",
        "review_token_hash": "VARCHAR(128)",
        "revision_rounds_allowed": "INTEGER DEFAULT 2",
        "revision_rounds_used": "INTEGER DEFAULT 0",
        "revision_requests": json_type,
        "final_approved_at": dt_type,
        "final_zip_url": "TEXT",
    }

    with app_engine.begin() as conn:
        for name, ddl in columns.items():
            if name in existing:
                continue
            statement = f"ALTER TABLE orders ADD COLUMN {name} {ddl}"
            try:
                conn.execute(text(statement))
            except Exception as exc:
                print(f"[DB WARNING] order migration failed for {name}: {exc}")
