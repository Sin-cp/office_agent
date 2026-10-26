"""Run from backend/: python -m uvicorn app.main:app --reload."""

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from . import database
from .files import router
from .storage import data_directory


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.data_dir = data_directory()
    database.initialize(app.state.data_dir)
    yield


app = FastAPI(title="Office Agent API", version="0.2.0", lifespan=lifespan)
app.include_router(router)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "office-agent"
    version: str = "0.2.0"


@app.get("/api/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    return HealthResponse()
