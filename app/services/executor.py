import tempfile
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

SANDBOX_IMAGE = "automation-sandbox:latest"


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

    with tempfile.TemporaryDirectory() as tmp_dir:
        script_path = Path(tmp_dir) / "script.py"
        script_path.write_text(automation.script_code, encoding="utf-8")

        client = docker.from_env()
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
                network_disabled=False,
                detach=True,
            )
            try:
                result = container.wait(timeout=settings.execution_timeout_seconds)
                exit_code = result.get("StatusCode", 1)
                execution.output_log = container.logs().decode("utf-8", errors="replace")
                execution.status = (
                    ExecutionStatus.SUCCESS if exit_code == 0 else ExecutionStatus.FAILED
                )
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
                # En Windows (Docker Desktop, transporte via named pipe) un
                # timeout de wait() sale como ConnectionError, no ReadTimeout
                # directo como en Linux/Mac -- hay que atrapar los dos.
                container.kill()
                partial_logs = container.logs().decode("utf-8", errors="replace")
                execution.output_log = (
                    f"Ejecución cancelada: superó el timeout de "
                    f"{settings.execution_timeout_seconds}s.\n"
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
                container.remove(force=True)

    execution.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(execution)
    return execution
