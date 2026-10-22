"""
Tests lentos de integracion real del scheduler.

A diferencia de tests/test_automation.py, estos NO usan las fixtures
client/db_session del conftest (que aislan con una transaccion que se
revierte al final). El job del scheduler corre en un hilo aparte con su
propia sesion (ver app/services/scheduler.py: usa SessionLocal de
app.database directamente, no pasa por get_db ni por
app.dependency_overrides), y una conexion de Postgres no puede ver datos
todavia no comiteados de otra conexion/transaccion. Por eso estos tests
usan la base real configurada (DATABASE_URL) y limpian a mano lo que crean.

Desde la Fase 5 el scheduler no vive en la API sino en el worker
(app/worker.py): el test arranca el scheduler + el sync del worker en este
mismo proceso, igual que lo hace app.worker.main().

Correr con: pytest tests/test_scheduler_integration.py -m slow
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Automation, Execution, ExecutionStatus, User
from app.services.scheduler import start_scheduler, start_sync_job, stop_scheduler
from app.services.security import create_access_token, hash_password

pytestmark = pytest.mark.slow


def test_automation_with_minute_cron_runs_on_its_own():
    db = SessionLocal()
    user = db.query(User).first()
    created_user = user is None
    if created_user:
        user = User(
            email="scheduler-integration-test@example.com",
            hashed_password=hash_password("password123"),
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    auth_headers = {"Authorization": f"Bearer {create_access_token(user.id)}"}

    automation_id = None
    scheduler_started = False
    try:
        with TestClient(app) as client:
            response = client.post(
                "/automations",
                json={
                    "name": "Scheduler integration test",
                    "script_code": "print('corrida automatica')",
                    "cron_expression": "* * * * *",
                },
                headers=auth_headers,
            )
            assert response.status_code == 201
            automation_id = response.json()["id"]

            # La pasada inicial de start_sync_job ya levanta la automatización
            # recién creada; el intervalo largo es para que no corra otra vez
            # durante el test.
            start_scheduler()
            scheduler_started = True
            start_sync_job(interval_seconds=3600)

            deadline = time.monotonic() + 65
            execution = None
            while time.monotonic() < deadline:
                # expire_all() fuerza a releer de la DB en cada vuelta --
                # sin esto, la identity map de SQLAlchemy puede devolver el
                # mismo objeto Python ya cacheado en RUNNING de una vuelta
                # anterior, en vez de la fila actualizada.
                db.expire_all()
                execution = (
                    db.query(Execution)
                    .filter(Execution.automation_id == automation_id)
                    .first()
                )
                # No alcanza con que exista: la Execution se crea en RUNNING
                # ANTES de que el container termine (ver executor.py), así
                # que hay que esperar a que llegue a un estado final.
                if execution is not None and execution.status != ExecutionStatus.RUNNING:
                    break
                time.sleep(2)

            assert execution is not None, "la automatizacion no corrio sola dentro de 65s"
            assert execution.status == ExecutionStatus.SUCCESS
            assert "corrida automatica" in execution.output_log
    finally:
        if scheduler_started:
            stop_scheduler()
        if automation_id is not None:
            automation = db.get(Automation, automation_id)
            if automation is not None:
                db.delete(automation)  # cascade borra la Execution tambien
                db.commit()
        if created_user:
            db.delete(user)
            db.commit()
        db.close()
