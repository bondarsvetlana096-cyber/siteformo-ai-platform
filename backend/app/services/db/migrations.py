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
