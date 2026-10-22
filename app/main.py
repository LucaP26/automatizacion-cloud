from fastapi import FastAPI

from app.routers import auth, automations

# Desde la Fase 5 la API no corre el scheduler: vive en su propio proceso
# (app/worker.py), que se entera de los cambios releyendo la base. Por eso
# ya no hay lifespan acá.
app = FastAPI(title="Automation Manager", version="0.1.0")

app.include_router(auth.router)
app.include_router(automations.router)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}
