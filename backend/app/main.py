"""TwinPilot backend entry point: `uv run python -m uvicorn app.main:app --app-dir backend`."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="TwinPilot AI",
        description="Simulation-based digital twin prototype (Level 1). All sensors are simulated.",
        version="0.1.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "level": "L1-simulation"}

    return app


app = create_app()
