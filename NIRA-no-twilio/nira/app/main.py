import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from .config import get_settings
from .database import Base, engine
from . import models  # noqa: F401  (register tables)
from .errors import (AppError, app_error_handler, rate_limit_handler, unhandled_handler, validation_handler)
from .ratelimit import limiter
from . import scheduler
from .routers import ai_settings, assistant, auth, calls, deadlines, me

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings().check_production()
    Base.metadata.create_all(engine)   # Phase 1: simple. Switch to Alembic migrations before real users.
    scheduler.start_scheduler()          # no-op unless the four Twilio call settings are valid
    try:
        yield
    finally:
        scheduler.stop_scheduler()


# In production the interactive API docs (/docs, /redoc, /openapi.json) are switched off: nobody needs a map of the API there.
_docs = {} if not get_settings().is_prod else {"docs_url": None, "redoc_url": None, "openapi_url": None}
app = FastAPI(title="NIRA API", version="0.1.0", lifespan=lifespan, **_docs)
app.state.limiter = limiter
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RequestValidationError, validation_handler)
app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
app.add_exception_handler(Exception, unhandled_handler)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().origins, allow_methods=["*"],
                   allow_headers=["Authorization", "Content-Type"])

app.include_router(auth.router)
app.include_router(me.router)
app.include_router(ai_settings.router)
app.include_router(deadlines.router)
app.include_router(assistant.router)
app.include_router(calls.router)
app.include_router(calls.webhook)


@app.get("/health")
def health():
    return {"ok": True}

# Frontend: served by the same server, so one command runs everything (must stay LAST).
from pathlib import Path  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
app.mount("/", StaticFiles(directory=Path(__file__).resolve().parent.parent / "static", html=True), name="static")
