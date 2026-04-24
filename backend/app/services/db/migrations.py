from sqlalchemy import text

from app.services.db.postgres import engine


def run_lightweight_migrations() -> None:
    if engine is None:
        return

    with engine.begin() as conn:
        try:
            conn.execute(text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS status VARCHAR(50) DEFAULT 'new'"))
        except Exception as exc:
            print(f"[DB WARNING] lightweight migration failed: {exc}")
