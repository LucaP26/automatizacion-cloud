from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings


def hash_password(password: str) -> str:
    """Hashea una contraseña con bcrypt (incluye salt aleatorio por diseño).

    bcrypt en vez de passlib: passlib es una capa de abstracción más amplia
    (soporta decenas de esquemas) que hace tiempo no recibe mantenimiento
    activo; bcrypt a secas es la librería específica, simple y battle-tested
    para este único propósito.
    """
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.jwt_access_token_expire_minutes
    )
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Decodifica y valida un JWT. Tira jwt.InvalidTokenError (o una
    subclase, como ExpiredSignatureError) si el token expiró, la firma no
    coincide, o está corrupto -- el llamador (get_current_user) la traduce
    a un 401.
    """
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
