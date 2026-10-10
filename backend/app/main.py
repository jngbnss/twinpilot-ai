"""TwinPilot backend entry point.

Run: uv run python -m uvicorn app.main:app --app-dir backend --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.anomalies import router as anomalies_router
from app.api.routes import router, ws_router
from app.core.config import Settings, get_settings
from app.engine import Engine


def build_anomaly_hook(settings: Settings):
    """Phase 3 plugs the read-only anomaly service in here."""
    try:
        from app.anomaly.service import AnomalyService
    except ImportError:
        return None
    return AnomalyService.from_settings(settings)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = Engine(settings, anomaly=build_anomaly_hook(settings))
        app.state.engine = engine
        await engine.start()
        yield
        await engine.shutdown()

    app = FastAPI(
        title="TwinPilot AI",
        description="Simulation-based digital twin prototype (Level 1). All sensors are simulated.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    app.include_router(anomalies_router)
    app.include_router(ws_router)
    return app


app = create_app()
