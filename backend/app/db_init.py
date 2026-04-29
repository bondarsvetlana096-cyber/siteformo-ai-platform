from app.db.session import engine
from app.db.base import Base

# 🔥 ОБЯЗАТЕЛЬНО импортируем ВСЕ модели
from app.models.order import Order

# если есть — добавь:
# from app.models.client import ClientProfile


def init_db():
    print("🔥 FORCE creating all tables...")

    Base.metadata.create_all(bind=engine)

    print("✅ Tables created")