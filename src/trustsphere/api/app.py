"""FastAPI application factory — the one process Streamlit, Joule-equivalent
Investigation Assistant, and SAP Build Process Automation all call into
(CLAUDE.md §13 "Create a backend service boundary").

Run locally with:
    uvicorn trustsphere.api.app:app --reload --port 8000

Every route lives in `trustsphere.api.routers.*`; this module only wires them
together, sets up structured logging, and registers the stable error-handling
contract from `trustsphere.api.errors`.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from trustsphere.api.errors import register_error_handlers
from trustsphere.api.routers import alerts, cases, explanations, health
from trustsphere.config import get_settings


def _configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def create_app() -> FastAPI:
    _configure_logging()
    settings = get_settings()

    app = FastAPI(
        title="TrustSphere RiskOps Copilot — Track A API",
        description=(
            "Deterministic urgency scoring, advisory SLA prediction, and "
            "HybridRAG CaseFile assembly. Backend: "
            f"{settings.data_backend} (see /health for the live label)."
        ),
        version="0.1.0",
    )

    register_error_handlers(app)

    app.include_router(health.router)
    app.include_router(alerts.router)
    app.include_router(cases.router)
    app.include_router(explanations.router)  # Track B generation (B2)

    @app.get("/")
    def root():
        return {
            "service": "trustsphere-track-a",
            "docs": "/docs",
            "health": "/health",
        }

    return app


app = create_app()
