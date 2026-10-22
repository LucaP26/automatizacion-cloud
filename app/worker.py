"""Proceso worker: corre el scheduler, separado de la API.

Uso: python -m app.worker

Hasta la Fase 4 el scheduler vivía adentro del proceso de la API (en el
lifespan de FastAPI). Eso ata la API a una sola réplica: con dos réplicas
(o `uvicorn --workers 2`) habría dos schedulers y cada cron se dispararía
dos veces. Con el scheduler en su propio proceso, la API puede escalar
horizontalmente y el worker queda como la única instancia que agenda.
"""

import logging
import signal
import threading

from app.config import settings
from app.services.scheduler import start_scheduler, start_sync_job, stop_scheduler

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # APScheduler loguea en INFO cada vez que corre un job, incluido el sync
    # cada 30s: puro ruido. Los eventos que importan los loguea scheduler.py.
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    stop = threading.Event()
    # SIGTERM es lo que manda `docker stop` / `docker compose down`; SIGINT
    # es Ctrl+C corriéndolo a mano.
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    start_scheduler()
    start_sync_job(settings.scheduler_sync_interval_seconds)
    logger.info(
        "Worker arrancado; sincroniza con la base cada %ss.",
        settings.scheduler_sync_interval_seconds,
    )

    # wait() con timeout en un loop, no wait() a secas: en Windows un wait
    # sin timeout no se interrumpe con Ctrl+C.
    while not stop.wait(timeout=1):
        pass

    logger.info("Apagando worker (esperando ejecuciones en curso)...")
    stop_scheduler()


if __name__ == "__main__":
    main()
