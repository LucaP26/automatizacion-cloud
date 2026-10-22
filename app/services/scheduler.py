import logging

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.automation import Automation
from app.services.executor import run_automation_script

logger = logging.getLogger(__name__)

SYNC_JOB_ID = "scheduler_sync"

scheduler = BackgroundScheduler()

# automation_id -> cron_expression con el que está agendado ahora mismo.
# Es lo que permite que cada sync sea un diff contra la base (agregar lo
# nuevo, reagendar lo que cambió de cron, sacar lo borrado/desactivado) en
# vez de borrar y recrear todos los jobs cada N segundos. Solo lo toca
# sync_scheduled_automations, que corre como un job con max_instances=1
# (default de APScheduler): nunca hay dos syncs concurrentes pisándolo.
_scheduled_crons: dict[int, str] = {}


def start_scheduler() -> None:
    """Arranca el scheduler con una instancia NUEVA, no reutiliza la anterior.

    BackgroundScheduler.start() despues de un shutdown() no revive su
    ThreadPoolExecutor -- queda muerto para siempre, y los jobs que se
    agendan despues fallan en silencio (APScheduler solo lo loguea
    internamente, nunca levanta la excepcion). Esto importa en los tests,
    que arrancan y apagan el scheduler varias veces en el mismo proceso.
    """
    global scheduler
    scheduler = BackgroundScheduler()
    _scheduled_crons.clear()
    scheduler.start()


def stop_scheduler() -> None:
    # wait=True: si hay una ejecución en curso, se espera a que termine (y
    # persista su resultado) antes de salir, en vez de dejar la Execution
    # colgada en RUNNING. Por eso el worker tiene un stop_grace_period más
    # largo que el timeout de ejecución en docker-compose.yml.
    scheduler.shutdown(wait=True)


def start_sync_job(interval_seconds: int) -> None:
    """Agenda la reconciliación periódica contra la tabla `automations`.

    Desde la Fase 5 el scheduler vive en un proceso aparte (app/worker.py),
    así que la API ya no puede llamarlo directo al crear/editar/borrar una
    automatización. En vez de un canal de mensajes entre los dos procesos,
    el worker relee la base cada `interval_seconds`: la tabla ya era la
    fuente de verdad del schedule desde la Fase 2. `next_run_time` vacío en
    add_job = esperar el primer intervalo; por eso se hace una pasada
    inmediata acá, para no arrancar con 30s sin jobs.
    """
    _sync_job()
    scheduler.add_job(
        _sync_job,
        trigger="interval",
        seconds=interval_seconds,
        id=SYNC_JOB_ID,
        replace_existing=True,
    )


def _sync_job() -> None:
    db = SessionLocal()
    try:
        sync_scheduled_automations(db)
    except Exception:
        # Una base caída un rato no tiene que matar el job de sync para
        # siempre: se loguea y se reintenta en el próximo intervalo, con
        # los jobs que ya estaban agendados siguiendo como estaban.
        logger.exception("Falló la sincronización del scheduler; se reintenta en el próximo ciclo.")
    finally:
        db.close()


def _run_automation_job(automation_id: int) -> None:
    db = SessionLocal()
    try:
        automation = db.get(Automation, automation_id)
        # Chequeo defensivo: entre un sync y el siguiente, la automatización
        # pudo haberse desactivado o borrado desde la API.
        if automation is None or not automation.is_active:
            return
        execution = run_automation_script(db, automation)
        logger.info(
            "Ejecución por cron de automation_id=%s terminó en %s (execution_id=%s)",
            automation_id,
            execution.status.value,
            execution.id,
        )
    finally:
        db.close()


def _job_id(automation_id: int) -> str:
    return f"automation_{automation_id}"


def sync_scheduled_automations(db: Session) -> None:
    desired = {
        automation_id: cron
        for automation_id, cron in db.query(Automation.id, Automation.cron_expression)
        .filter(Automation.is_active.is_(True))
        .all()
    }

    for automation_id in set(_scheduled_crons) - set(desired):
        try:
            scheduler.remove_job(_job_id(automation_id))
        except JobLookupError:
            pass
        del _scheduled_crons[automation_id]
        logger.info("Desagendada automation_id=%s", automation_id)

    for automation_id, cron in desired.items():
        if _scheduled_crons.get(automation_id) == cron:
            continue
        try:
            scheduler.add_job(
                _run_automation_job,
                trigger=CronTrigger.from_crontab(cron),
                args=[automation_id],
                id=_job_id(automation_id),
                replace_existing=True,
            )
            logger.info("Agendada automation_id=%s con cron %r", automation_id, cron)
        except ValueError:
            # El schema ya valida el cron al crear/editar, así que esto solo
            # pasa con un cron editado a mano en la base. Se registra igual
            # en _scheduled_crons para no re-loguear el mismo error en cada
            # ciclo; si alguien lo corrige, el cron cambia y se reintenta.
            logger.exception(
                "No se pudo agendar automation_id=%s (cron_expression=%r); se omite.",
                automation_id,
                cron,
            )
        _scheduled_crons[automation_id] = cron
