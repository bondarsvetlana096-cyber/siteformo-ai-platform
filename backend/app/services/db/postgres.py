import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = (
    os.getenv("DATABASE_URL")
    or os.getenv("POSTGRES_URL")
    or os.getenv("POSTGRESQL_URL")
    or os.getenv("SUPABASE_DB_URL")
)


def normalize_database_url(url: str | None) -> str | None:
    if not url:
        return None

    url = url.strip().strip('"').strip("'")

    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)

    if url.startswith("sqlite://") and ("@db." in url or "supabase.co" in url or ":5432/" in url or "postgres:" in url):
        return "postgresql://" + url.replace("sqlite://", "", 1).lstrip("/")

    return url


DATABASE_URL = normalize_database_url(DATABASE_URL)

engine = None
SessionLocal = None

if DATABASE_URL:
    try:
        engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    except Exception as exc:
        print(f"[DB WARNING] Invalid DATABASE_URL. Postgres disabled. Error: {exc}")
        engine = None
        SessionLocal = None
else:
    print("[DB WARNING] DATABASE_URL is not set. Postgres leads storage disabled.")
