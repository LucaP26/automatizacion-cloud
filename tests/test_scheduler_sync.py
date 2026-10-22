"""Tests de la reconciliación del worker contra la tabla `automations`.

A diferencia de tests/test_scheduler_integration.py, acá no se espera a que
un cron dispare de verdad: solo se verifica qué jobs quedan agendados
después de cada sync, así que alcanza con la sesión transaccional de
siempre (sync_scheduled_automations recibe la sesión por parámetro).
"""

import pytest

from app.models import Automation
from app.services import scheduler as scheduler_module
from app.services.scheduler import start_scheduler, stop_scheduler, sync_scheduled_automations


@pytest.fixture()
def running_scheduler():
    start_scheduler()
    yield
    stop_scheduler()


def _jobs_by_id() -> dict[str, str]:
    # El trigger se compara por su representación de texto: CronTrigger no
    # expone la expresión original, pero str() refleja los campos.
    return {job.id: str(job.trigger) for job in scheduler_module.scheduler.get_jobs()}


def _make_automation(db_session, user, **overrides) -> Automation:
    data = {
        "name": "Sync test",
        "script_code": "print('hola')",
        "cron_expression": "0 7 * * *",
        "user_id": user.id,
    }
    data.update(overrides)
    automation = Automation(**data)
    db_session.add(automation)
    db_session.commit()
    db_session.refresh(automation)
    return automation


def test_sync_schedules_only_active_automations(running_scheduler, db_session, test_user):
    active = _make_automation(db_session, test_user)
    inactive = _make_automation(db_session, test_user, is_active=False)

    sync_scheduled_automations(db_session)

    jobs = _jobs_by_id()
    assert f"automation_{active.id}" in jobs
    assert f"automation_{inactive.id}" not in jobs


def test_sync_picks_up_cron_changes(running_scheduler, db_session, test_user):
    automation = _make_automation(db_session, test_user, cron_expression="0 7 * * *")
    sync_scheduled_automations(db_session)
    before = _jobs_by_id()[f"automation_{automation.id}"]

    automation.cron_expression = "30 9 * * 1"
    db_session.commit()
    sync_scheduled_automations(db_session)

    after = _jobs_by_id()[f"automation_{automation.id}"]
    assert before != after
    assert "hour='9'" in after and "minute='30'" in after


def test_sync_removes_deactivated_and_deleted_automations(
    running_scheduler, db_session, test_user
):
    to_deactivate = _make_automation(db_session, test_user)
    to_delete = _make_automation(db_session, test_user)
    sync_scheduled_automations(db_session)
    assert len(_jobs_by_id()) == 2

    to_deactivate.is_active = False
    db_session.delete(to_delete)
    db_session.commit()
    sync_scheduled_automations(db_session)

    assert _jobs_by_id() == {}
