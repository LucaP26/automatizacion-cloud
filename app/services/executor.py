import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

import docker
import requests
from docker.errors import ImageNotFound
from sqlalchemy.orm import Session

from app.config import settings
from app.models.automation import Automation
from app.models.execution import Execution, ExecutionStatus
from app.services.crypto import decrypt_secrets
from app.services.notifier import notify_execution_failed

SANDBOX_IMAGE = "automation-sandbox:latest"

_docker_client: docker.DockerClient | None = None
_docker_client_lock = threading.Lock()


def _get_docker_client() -> docker.DockerClient:
    """Cliente de Docker cacheado como singleton perezoso.

    No se crea a nivel de módulo (al importar) porque `docker.from_env()`
    negocia la versión de la API contra el daemon al construirse -- si se
    hiciera al importar (es decir, al arrancar la app), Docker Desktop
    todavía no corriendo tiraría abajo el arranque completo de la API. Se
    crea perezoso, en el primer uso real, y se reusa después para no pagar
    esa negociación en cada ejecución.

    El lock evita que dos ejecuciones que arrancan al mismo tiempo (dos
    crons disparando juntos, o un cron solapado con un POST /run manual)
    creen cada una su propio cliente y una se pierda sin cerrar su conexión.
    """
    global _docker_client
    if _docker_client is None:
        with _docker_client_lock:
            if _docker_client is None:  # otro hilo pudo haberlo creado ya
                _docker_client = docker.from_env()
    return _docker_client


def run_automation_script(db: Session, automation: Automation) -> Execution:
    """Ejecuta el script de una automatización de forma síncrona y bloqueante.

    Fase 3: corre el código dentro de un container Docker descartable,
    reemplazando el `subprocess` de la Fase 1 -- código arbitrario de
    usuarios ya no corre con los mismos permisos ni el mismo filesystem
    que la API. El container tiene timeout, límites de memoria/CPU, y
    filesystem de solo lectura salvo un /tmp efímero. El router y el
    scheduler no se enteran de este cambio: la firma de esta función no
    se tocó.
    """
    execution = Execution(automation_id=automation.id, status=ExecutionStatus.RUNNING)
    execution.started_at = datetime.now(timezone.utc)
    db.add(execution)
    db.commit()
    db.refresh(execution)

    try:
        _run_in_sandbox(execution, automation)
    except Exception as exc:
        # Red de seguridad: sea lo que sea (daemon caído, SECRET_KEY
        # inválida, lo que no se nos haya ocurrido), la Execution nunca debe
        # quedar en RUNNING para siempre sin finished_at. Preferimos un
        # FAILED con el error real a un registro colgado invisible.
        execution.status = ExecutionStatus.FAILED
        execution.output_log = f"Error inesperado al ejecutar: {type(exc).__name__}: {exc}"

    execution.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(execution)

    # Se dispara desde el service, no desde el router: así notifica igual
    # si la corrida vino de un POST /run manual o del scheduler, sin
    # duplicar esta lógica en los dos lugares que llaman a esta función.
    if execution.status == ExecutionStatus.FAILED:
        notify_execution_failed(automation, execution)

    return execution


def _run_in_sandbox(execution: Execution, automation: Automation) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        script_path = Path(tmp_dir) / "script.py"
        script_path.write_text(automation.script_code, encoding="utf-8")

        # tempfile.TemporaryDirectory() crea el directorio en modo 0700
        # (solo el usuario que corre la API). El container corre con un UID
        # distinto y sin privilegios (ver docker/sandbox.Dockerfile), así
        # que en un host Linux real (a diferencia de Docker Desktop en
        # Windows, cuya capa de compartir archivos no aplica permisos POSIX
        # estrictos) ese UID no podría ni entrar al directorio montado.
        # Sin este chmod, cada ejecución fallaría con un error de permisos
        # apenas esto corra en un host Linux de verdad (CI, Azure).
        Path(tmp_dir).chmod(0o755)
        script_path.chmod(0o644)

        client = _get_docker_client()
        container = None
        try:
            container = client.containers.run(
                SANDBOX_IMAGE,
                command=["/sandbox/script.py"],
                volumes={tmp_dir: {"bind": "/sandbox", "mode": "ro"}},
                environment=decrypt_secrets(automation.secrets_encrypted),
                read_only=True,
                tmpfs={"/tmp": "rw,size=16m"},
                mem_limit=settings.execution_memory_limit,
                nano_cpus=int(settings.execution_cpu_limit * 1_000_000_000),
                detach=True,
            )
            try:
                result = container.wait(timeout=settings.execution_timeout_seconds)
                exit_code = result.get("StatusCode", 1)
                execution.output_log = container.logs().decode("utf-8", errors="replace")
                execution.status = (
                    ExecutionStatus.SUCCESS if exit_code == 0 else ExecutionStatus.FAILED
                )
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as exc:
                # En Windows (Docker Desktop, transporte via named pipe) un
                # timeout de wait() sale como ConnectionError, no ReadTimeout
                # directo como en Linux/Mac -- pero el mismo ConnectionError
                # también puede significar que se cayó la conexión con el
                # daemon por otro motivo. No hay forma confiable de
                # distinguir los dos casos con la info que da la excepción,
                # así que el mensaje no asume cuál pasó, y el kill()/logs()
                # de limpieza es defensivo (la conexión puede seguir rota).
                try:
                    container.kill()
                except Exception:
                    pass
                try:
                    partial_logs = container.logs().decode("utf-8", errors="replace")
                except Exception:
                    partial_logs = "(no se pudieron obtener logs parciales)"
                execution.output_log = (
                    f"Ejecución interrumpida: timeout de "
                    f"{settings.execution_timeout_seconds}s o se perdió la conexión "
                    f"con Docker ({type(exc).__name__}).\n"
                    f"Logs parciales:\n{partial_logs}"
                )
                execution.status = ExecutionStatus.FAILED
        except ImageNotFound:
            execution.output_log = (
                f"No se encontró la imagen '{SANDBOX_IMAGE}'. Construila con:\n"
                f"docker build -f docker/sandbox.Dockerfile -t {SANDBOX_IMAGE} docker"
            )
            execution.status = ExecutionStatus.FAILED
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
