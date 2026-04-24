from app.services.db.postgres import engine
from app.services.db.models import Base


def init_db() -> None:
    if engine is not None:
        Base.metadata.create_all(bind=engine)
