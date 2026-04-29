from app.db.session import engine
from app.db.base import Base

# импорт ВСЕХ моделей (ОЧЕНЬ ВАЖНО)
from app.models.order import Order
from app.models.client import ClientProfile  # если есть
# добавь сюда другие модели если есть


def init_db():
    print("🔥 Creating database tables if not exist...")
    Base.metadata.create_all(bind=engine)
    print("✅ Database ready")