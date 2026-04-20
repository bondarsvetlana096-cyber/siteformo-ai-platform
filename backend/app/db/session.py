from __future__ import annotations

import logging

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

logger = logging.getLogger("siteformo.db")


def _build_engine_kwargs(db_url: str) -> dict:
    parsed = make_url(db_url)
    connect_args = {}
    if parsed.drivername.startswith("postgresql") and "+psycopg" in parsed.drivername:
        connect_args["prepare_threshold"] = None
    kwargs = {"pool_pre_ping": True}
    if connect_args:
        kwargs["connect_args"] = connect_args
    return kwargs


db_url = settings.database_url
logger.info("DATABASE_URL_RAW=%r", db_url)

try:
    parsed = make_url(db_url)
    logger.info(
        "DB_PARSED driver=%s host=%s port=%s db=%s user=%s",
        parsed.drivername,
        parsed.host,
        parsed.port,
        parsed.database,
        parsed.username,
    )
except Exception:
    logger.exception("DB_URL_PARSE_ERROR")

engine = create_engine(db_url, **_build_engine_kwargs(db_url))

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
