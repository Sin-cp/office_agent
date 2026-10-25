"""Office Agent API. Run from backend/: python -m uvicorn app.main:app --reload."""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Office Agent API", version="0.1.0")


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "office-agent"
    version: str = "0.1.0"


@app.get("/api/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    """Confirm the API process is serving requests (no external dependencies yet)."""
    return HealthResponse()
