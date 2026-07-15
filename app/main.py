from contextlib import asynccontextmanager

from fastapi import FastAPI

from .routers import admin, auth, chat, usage
from .storage import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="LLM Gateway with Metering",
    description="A teaching-scale LLM gateway: multi-provider routing, "
    "usage normalization, Decimal rating, idempotent billing, verification.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(usage.router)
app.include_router(admin.router)


@app.get("/healthz", tags=["ops"])
def health() -> dict:
    return {"status": "ok"}
