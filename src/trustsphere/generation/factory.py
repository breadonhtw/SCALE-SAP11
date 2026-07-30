"""Backend selection: real orchestration when configured, else fallback."""

from __future__ import annotations

import os

from .base import Generator
from .fallback import FallbackGenerator


def get_generator() -> Generator:
    backend = os.environ.get("GENERATION_BACKEND", "auto")
    if backend == "fallback":
        return FallbackGenerator()
    try:
        from .orchestration import OrchestrationGenerator
        return OrchestrationGenerator()  # raises if creds/model not configured
    except Exception:
        if backend == "sap_ai_core":
            raise  # explicitly requested — surface the configuration error
        return FallbackGenerator()
