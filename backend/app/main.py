from fastapi import FastAPI

from app.api.routes import router
from app.api.channel_routes import router as channel_router
from app.core.config import settings
from app.db.session import Base, engine
from app.models.conversation import ConversationMessage, ConversationSession
from app.models.order import ClientProfile, FinalPackage, HomepageConcept, Order

app = FastAPI(title=settings.app_name)
app.include_router(router)
app.include_router(channel_router)

Base.metadata.create_all(bind=engine)
