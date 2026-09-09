from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.execution import ExecutionStatus


class ExecutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    automation_id: int
    status: ExecutionStatus
    output_log: str | None
    started_at: datetime | None
    finished_at: datetime | None
