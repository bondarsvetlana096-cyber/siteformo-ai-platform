from app.services.db.postgres import engine
from app.services.db.models import Base
from app.services.db.migrations import run_lightweight_migrations


def init_db() -> None:
    if engine is not None:
        Base.metadata.create_all(bind=engine)
        run_lightweight_migrations()
