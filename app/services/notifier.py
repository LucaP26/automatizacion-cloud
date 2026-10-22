import logging

import requests

from app.config import settings
from app.models.automation import Automation
from app.models.execution import Execution

logger = logging.getLogger(__name__)


def notify_execution_failed(automation: Automation, execution: Execution) -> None:
    """Avisa por webhook (p.ej. Slack) cuando una ejecución termina en failed.

    Si no hay NOTIFICATION_WEBHOOK_URL configurada, no hace nada -- es una
    funcionalidad opt-in, no un requisito para que el resto de la app
    funcione. Una notificación que falla (webhook caído, timeout) nunca
    debe hacer fallar la ejecución en sí: se loguea y se sigue.
    """
    if not settings.notification_webhook_url:
        return

    log_preview = (execution.output_log or "")[:1500]
    payload = {
        "text": (
            f":rotating_light: La automatización *{automation.name}* "
            f"(id={automation.id}) falló.\n"
            f"Execution id={execution.id}\n"
            f"```{log_preview}```"
        )
    }
    try:
        requests.post(settings.notification_webhook_url, json=payload, timeout=5)
    except requests.RequestException:
        logger.exception(
            "No se pudo enviar la notificación de fallo para automation_id=%s",
            automation.id,
        )
