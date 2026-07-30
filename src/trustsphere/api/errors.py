"""Stable application error type + FastAPI exception handler (CLAUDE.md §13).

Vendor/DB exceptions must never leak raw messages to a client — they're
converted here into a stable code + user-safe message, with the original
exception logged (not returned).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class NotFoundError(AppError):
    def __init__(self, message: str):
        super().__init__("NOT_FOUND", message, status_code=404)


class ConflictError(AppError):
    def __init__(self, message: str):
        super().__init__("CONFLICT", message, status_code=409)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error_code": exc.code, "message": exc.message},
        )

    @app.exception_handler(Exception)
    async def _unhandled_error_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error_code": "INTERNAL_ERROR", "message": "An unexpected error occurred."},
        )
