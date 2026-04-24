from app.services.db.postgres import engine
from app.services.db.models import Base as LeadBase
from app.services.db.migrations import run_lightweight_migrations


def init_db() -> None:
    if engine is not None:
        LeadBase.metadata.create_all(bind=engine)
        run_lightweight_migrations()

    # Guided web-chat sessions use the main SQLAlchemy Base/engine.
    # Import models here so metadata contains conversation tables before create_all.
    try:
        from app.db.session import Base as AppBase, engine as app_engine
        from app.models import conversation  # noqa: F401
        from app.models import order  # noqa: F401

        AppBase.metadata.create_all(bind=app_engine)
    except Exception as exc:
        print(f"[DB WARNING] Guided web-chat tables were not initialized. Error: {exc}")
