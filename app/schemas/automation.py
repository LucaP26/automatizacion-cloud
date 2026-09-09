from datetime import datetime

from croniter import croniter
from pydantic import BaseModel, ConfigDict, field_validator


class AutomationBase(BaseModel):
    name: str
    script_code: str
    cron_expression: str
    is_active: bool = True

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, value: str) -> str:
        if not croniter.is_valid(value):
            raise ValueError(f"'{value}' no es una expresión cron válida")
        return value


class AutomationCreate(AutomationBase):
    user_id: int


class AutomationUpdate(BaseModel):
    name: str | None = None
    script_code: str | None = None
    cron_expression: str | None = None
    is_active: bool | None = None

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, value: str | None) -> str | None:
        if value is not None and not croniter.is_valid(value):
            raise ValueError(f"'{value}' no es una expresión cron válida")
        return value


class AutomationRead(AutomationBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime
