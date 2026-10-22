from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.automation import Automation
from app.models.execution import Execution
from app.models.user import User
from app.schemas.automation import AutomationCreate, AutomationRead, AutomationUpdate
from app.schemas.execution import ExecutionRead
from app.services.crypto import encrypt_secrets
from app.services.executor import run_automation_script
from app.services.scheduler import schedule_automation, unschedule_automation

router = APIRouter(prefix="/automations", tags=["automations"])


def _get_owned_automation_or_404(
    db: Session, automation_id: int, current_user: User
) -> Automation:
    # Filtra por id Y por dueño en la misma query, y devuelve 404 en
    # cualquiera de los dos casos (no existe, o existe pero es de otro
    # usuario) -- nunca un 403 que confirmaría que el id existe. Así, un
    # usuario no puede ni enterarse de si el id de otro existe o no.
    # Recibe el User completo (no solo su id) para que cada call site pase
    # el objeto que ya tiene a mano, sin tener que acordarse de ".id".
    automation = (
        db.query(Automation)
        .filter(Automation.id == automation_id, Automation.user_id == current_user.id)
        .first()
    )
    if automation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Automation not found")
    return automation


@router.post("", response_model=AutomationRead, status_code=status.HTTP_201_CREATED)
def create_automation(
    payload: AutomationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Automation:
    data = payload.model_dump()
    secrets = data.pop("secrets")
    automation = Automation(
        **data, user_id=current_user.id, secrets_encrypted=encrypt_secrets(secrets)
    )
    db.add(automation)
    db.commit()
    db.refresh(automation)
    if automation.is_active:
        schedule_automation(automation)
    return automation


@router.get("", response_model=list[AutomationRead])
def get_automations(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> list[Automation]:
    return db.query(Automation).filter(Automation.user_id == current_user.id).all()


@router.get("/{automation_id}", response_model=AutomationRead)
def get_automation(
    automation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Automation:
    return _get_owned_automation_or_404(db, automation_id, current_user)


@router.patch("/{automation_id}", response_model=AutomationRead)
def update_automation(
    automation_id: int,
    payload: AutomationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Automation:
    automation = _get_owned_automation_or_404(db, automation_id, current_user)
    updated_fields = payload.model_dump(exclude_unset=True)
    if "secrets" in updated_fields:
        automation.secrets_encrypted = encrypt_secrets(updated_fields.pop("secrets"))
    for key, value in updated_fields.items():
        setattr(automation, key, value)
    db.commit()
    db.refresh(automation)
    if "cron_expression" in updated_fields or "is_active" in updated_fields:
        if automation.is_active:
            schedule_automation(automation)
        else:
            unschedule_automation(automation.id)
    return automation


@router.delete("/{automation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_automation(
    automation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    automation = _get_owned_automation_or_404(db, automation_id, current_user)
    db.delete(automation)
    db.commit()
    unschedule_automation(automation_id)
    return None


@router.post("/{automation_id}/run", response_model=ExecutionRead, status_code=status.HTTP_201_CREATED)
def run_automation(
    automation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Execution:
    automation = _get_owned_automation_or_404(db, automation_id, current_user)
    return run_automation_script(db, automation)


@router.get("/{automation_id}/executions", response_model=list[ExecutionRead])
def get_automation_executions(
    automation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Execution]:
    automation = _get_owned_automation_or_404(db, automation_id, current_user)
    return automation.executions


@router.get("/{automation_id}/executions/{execution_id}", response_model=ExecutionRead)
def get_automation_execution(
    automation_id: int,
    execution_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Execution:
    _get_owned_automation_or_404(db, automation_id, current_user)
    # Mismo estilo que _get_owned_automation_or_404: filtrar por los dos
    # campos en la query, en vez de traer por PK y comparar en Python.
    execution = (
        db.query(Execution)
        .filter(Execution.id == execution_id, Execution.automation_id == automation_id)
        .first()
    )
    if execution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found")
    return execution
