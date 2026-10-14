from datetime import datetime

from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, ConfigDict, field_validator


def _validate_cron(value: str) -> str:
    # Se valida con el mismo parser que despues usa el scheduler
    # (CronTrigger.from_crontab), no con croniter: croniter acepta
    # extensiones ("L", "1#2") que CronTrigger rechaza, y antes esa
    # discrepancia dejaba pasar automatizaciones que crasheaban el
    # arranque de la app al intentar agendarlas.
    try:
        CronTrigger.from_crontab(value)
    except ValueError as exc:
        raise ValueError(f"'{value}' no es una expresión cron válida: {exc}") from exc
    return value


class AutomationBase(BaseModel):
    name: str
    script_code: str
    cron_expression: str
    is_active: bool = True

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, value: str) -> str:
        return _validate_cron(value)


class AutomationCreate(AutomationBase):
    user_id: int
    # Env vars de la automatizacion (p.ej. API keys). Se encriptan antes de
    # guardarse (ver crypto.py) y nunca viajan de vuelta en una respuesta --
    # por eso vive solo en Create/Update, no en AutomationBase/Read.
    secrets: dict[str, str] | None = None


class AutomationUpdate(BaseModel):
    name: str | None = None
    script_code: str | None = None
    cron_expression: str | None = None
    is_active: bool | None = None
    secrets: dict[str, str] | None = None

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_cron(value)


class AutomationRead(AutomationBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime
