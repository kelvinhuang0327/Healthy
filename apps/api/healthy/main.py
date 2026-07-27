from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from healthy.infrastructure.config import Settings
from healthy.infrastructure.database import Database
from healthy.presentation.api import router


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    app = FastAPI(
        title="Healthy API",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = resolved_settings
    app.state.database = Database(resolved_settings.database_url)

    @app.exception_handler(RequestValidationError)
    async def safe_request_validation_error(
        _request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        # Pydantic validation details can include the rejected input. A generic
        # response prevents submitted credentials from being reflected.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": "Invalid request"},
        )

    app.include_router(router)
    return app


app = create_app()
