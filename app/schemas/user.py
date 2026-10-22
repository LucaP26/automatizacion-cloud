from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)

    @field_validator("password")
    @classmethod
    def validate_password_byte_length(cls, value: str) -> str:
        # bcrypt (>=4.0, la version que usamos) RECHAZA con ValueError una
        # password de mas de 72 BYTES -- no caracteres. "ñ"*72 son 72
        # caracteres pero 144 bytes en UTF-8, asi que un max_length=72 de
        # Pydantic (que cuenta caracteres) no alcanza para evitar ese 500.
        if len(value.encode("utf-8")) > 72:
            raise ValueError("La contraseña no puede superar los 72 bytes")
        return value


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
