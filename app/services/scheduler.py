from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.automation import Automation
from app.services.executor import run_automation_script

scheduler = BackgroundScheduler()


def _run_automation_job(automation_id: int) -> None:
    db = SessionLocal()
    try:
        automation = db.get(Automation, automation_id)
        if automation is None or not automation.is_active:
            return
        run_automation_script(db, automation)
    finally:
        db.close()


def schedule_automation(automation: Automation) -> None:
    scheduler.add_job(
        _run_automation_job,
        trigger=CronTrigger.from_crontab(automation.cron_expression),
        args=[automation.id],
        id=f"automation_{automation.id}",
        replace_existing=True,
    )


def unschedule_automation(automation_id: int) -> None:
    try:
        scheduler.remove_job(f"automation_{automation_id}")
    except JobLookupError:
        pass


def load_scheduled_automations(db: Session) -> None:
    automations = db.query(Automation).filter(Automation.is_active.is_(True)).all()
    for automation in automations:
        schedule_automation(automation)
