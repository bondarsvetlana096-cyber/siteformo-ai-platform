from fastapi import FastAPI

from app.api.channel_routes import router as channel_router
from app.api.routes import router as api_router
from app.db.session import Base, engine

Base.metadata.create_all(bind=engine)

app = FastAPI(title="SiteFormo AI Sales Platform")

app.include_router(channel_router)
app.include_router(api_router)


@app.get("/")
async def root():
    return {"ok": True}


@app.get("/health")
async def health():
    return {"status": "ok"}