from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    # bcrypt ignora/trunca silenciosamente cualquier byte despues del 72,
    # asi que lo limitamos antes -- un error 422 claro es mejor que un
    # hash que en secreto solo considera el primer pedazo de la password.
    password: str = Field(min_length=8, max_length=72)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
