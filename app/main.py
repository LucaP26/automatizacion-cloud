from fastapi import FastAPI

from app.routers import automations

app = FastAPI(title="Automation Manager", version="0.1.0")

app.include_router(automations.router)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}
