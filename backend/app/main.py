from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.channel_routes import router as channel_router
from app.api.routes import router as api_router


app = FastAPI(
    title="SiteFormo AI Sales Platform",
    version="1.0.0"
)

# CORS (на всякий случай)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ ОСНОВНЫЕ API
app.include_router(api_router)

# ✅ КАНАЛЫ (ВАЖНО!)
app.include_router(channel_router)


@app.get("/")
def root():
    return {"status": "ok"}


@app.get("/health")
def health():
    return {"status": "ok"}