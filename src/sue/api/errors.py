"""Единый формат ошибок API."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from sue.request_context import current_request_id

logger = logging.getLogger(__name__)


def api_error(status_code: int, code: str, message: str, **details: Any) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, "details": details or None},
    )


def _body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            payload = _body(
                str(exc.detail["code"]),
                str(exc.detail.get("message", "")),
                exc.detail.get("details"),
            )
        else:
            payload = _body(f"http_{exc.status_code}", str(exc.detail))
        return JSONResponse(payload, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            _body(
                "validation_error",
                "Некорректные параметры запроса",
                {"errors": exc.errors()},
            ),
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "Необработанная ошибка",
            extra={
                "path": request.url.path,
                "method": request.method,
                "request_id": current_request_id() or None,
            },
        )
        return JSONResponse(
            _body("internal_error", "Внутренняя ошибка сервера"),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
