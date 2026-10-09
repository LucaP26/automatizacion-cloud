from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import SessionLocal
from app.routers import automations
from app.services.scheduler import load_scheduled_automations, scheduler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Arranca el scheduler ANTES de cargar los jobs: add_job con
    # replace_existing=True solo deduplica contra un scheduler ya corriendo
    # (ver CLAUDE_PROGRESS.md).
    scheduler.start()
    db = SessionLocal()
    try:
        load_scheduled_automations(db)
    finally:
        db.close()

    yield

    scheduler.shutdown()


app = FastAPI(title="Automation Manager", version="0.1.0", lifespan=lifespan)

app.include_router(automations.router)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}
