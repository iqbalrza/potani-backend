"""Format error seragam: {"error": {"code", "message"}} tanpa membocorkan detail internal (aturan 8)."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)

_DEFAULT_MESSAGES = {
    401: ("unauthorized", "Silakan masuk terlebih dahulu."),
    403: ("forbidden", "Kamu tidak punya akses ke data ini."),
    404: ("not_found", "Data tidak ditemukan."),
    405: ("method_not_allowed", "Metode tidak didukung."),
    409: ("conflict", "Permintaan bentrok dengan data yang ada."),
    422: ("validation_error", "Data yang dikirim tidak valid."),
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _body(code: str, message: str, **extra) -> dict:
    return {"error": {"code": code, "message": message, **extra}}


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(_body(exc.code, exc.message), status_code=exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        code, message = _DEFAULT_MESSAGES.get(exc.status_code, ("error", "Permintaan tidak dapat diproses."))
        return JSONResponse(_body(code, message), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        fields = [".".join(str(p) for p in e.get("loc", ()) if p != "body") for e in exc.errors()]
        return JSONResponse(_body("validation_error", "Data yang dikirim tidak valid.", fields=fields),
                            status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled_error", extra={"path": request.url.path})
        return JSONResponse(_body("internal_error", "Terjadi kesalahan di server. Coba lagi sebentar lagi."),
                            status_code=500)
