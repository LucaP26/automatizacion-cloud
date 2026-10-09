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

Correr con: pytest tests/test_scheduler_integration.py -m slow
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Automation, Execution, ExecutionStatus, User

pytestmark = pytest.mark.slow


def test_automation_with_minute_cron_runs_on_its_own():
    db = SessionLocal()
    user = db.query(User).first()
    if user is None:
        user = User(email="scheduler-integration-test@example.com")
        db.add(user)
        db.commit()
        db.refresh(user)

    automation_id = None
    try:
        with TestClient(app) as client:
            response = client.post(
                "/automations",
                json={
                    "user_id": user.id,
                    "name": "Scheduler integration test",
                    "script_code": "print('corrida automatica')",
                    "cron_expression": "* * * * *",
                },
            )
            assert response.status_code == 201
            automation_id = response.json()["id"]

            deadline = time.monotonic() + 65
            execution = None
            while time.monotonic() < deadline:
                execution = (
                    db.query(Execution)
                    .filter(Execution.automation_id == automation_id)
                    .first()
                )
                if execution is not None:
                    break
                time.sleep(2)

            assert execution is not None, "la automatizacion no corrio sola dentro de 65s"
            assert execution.status == ExecutionStatus.SUCCESS
            assert "corrida automatica" in execution.output_log
    finally:
        if automation_id is not None:
            automation = db.get(Automation, automation_id)
            if automation is not None:
                db.delete(automation)  # cascade borra la Execution tambien
                db.commit()
        db.close()
