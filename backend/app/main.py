"""Run from backend/: python -m uvicorn app.main:app --reload."""

from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from . import database
from .tasks import Manager, router as tasks_router
from .files import router
from .projects import router as projects_router, recover_deletions
from .documents import router as documents_router
from .conversations import router as conversations_router
from .storage import data_directory


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv(Path(__file__).resolve().parents[2] / '.env', override=False)
    app.state.data_dir = data_directory()
    database.initialize(app.state.data_dir)
    recover_deletions(app.state.data_dir)
    app.state.tasks = Manager(app.state.data_dir)
    try:
        yield
    finally:
        await app.state.tasks.close()


app = FastAPI(title="Office Agent API", version="0.4.0", lifespan=lifespan)
app.include_router(router)
app.include_router(documents_router)
app.include_router(tasks_router)
app.include_router(projects_router)
app.include_router(conversations_router)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "office-agent"
    version: str = "0.4.0"


@app.get("/api/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    return HealthResponse()
