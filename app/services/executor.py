import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models.automation import Automation
from app.models.execution import Execution, ExecutionStatus


def run_automation_script(db: Session, automation: Automation) -> Execution:
    """Ejecuta el script de una automatización de forma síncrona y bloqueante.

    Fase 1: corre el código directamente con `subprocess` en el mismo host que
    la API, sin sandboxing. Es intencionalmente inseguro para código arbitrario
    de usuarios -- se reemplaza por ejecución en un container Docker aislado
    en la Fase 3.
    """
    execution = Execution(automation_id=automation.id, status=ExecutionStatus.RUNNING)
    execution.started_at = datetime.now(timezone.utc)
    db.add(execution)
    db.commit()
    db.refresh(execution)

    # delete=False porque en Windows no se puede reabrir un archivo que sigue
    # abierto por este mismo proceso (subprocess.run necesita abrirlo de nuevo
    # para leerlo) -- lo borramos a mano en el finally.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as script_file:
        script_file.write(automation.script_code)
        script_path = Path(script_file.name)

    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            timeout=settings.execution_timeout_seconds,
        )
        execution.output_log = result.stdout + result.stderr
        execution.status = (
            ExecutionStatus.SUCCESS if result.returncode == 0 else ExecutionStatus.FAILED
        )
    except subprocess.TimeoutExpired as exc:
        execution.output_log = (
            f"Ejecución cancelada: superó el timeout de "
            f"{settings.execution_timeout_seconds}s.\n"
            f"stdout parcial:\n{exc.stdout or ''}\nstderr parcial:\n{exc.stderr or ''}"
        )
        execution.status = ExecutionStatus.FAILED
    finally:
        script_path.unlink(missing_ok=True)

    execution.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(execution)
    return execution
