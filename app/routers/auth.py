from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.schemas.user import Token, UserCreate, UserRead
from app.services.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

# Hash fijo, precomputado una sola vez al importar el módulo. Se usa en
# login cuando el email no existe, para que bcrypt.checkpw corra de todas
# formas y el tiempo de respuesta no delate si el email está registrado o
# no (ver abajo) -- nunca es el hash real de ningún usuario.
_DUMMY_HASH = hash_password("esto-nunca-es-la-password-de-nadie")


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)) -> User:
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email ya registrado")

    user = User(email=payload.email, hashed_password=hash_password(payload.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Carrera: dos registros concurrentes con el mismo email pueden
        # pasar el chequeo de arriba los dos antes de que ninguno haga
        # commit -- el segundo commit choca con la constraint UNIQUE de
        # email, y eso tiene que traducirse a 409, no a un 500 sin más.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email ya registrado"
        ) from None
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)
) -> Token:
    # OAuth2PasswordRequestForm usa "username"/"password" como nombres de
    # campo (es el estandar del flujo password de OAuth2) -- aca "username"
    # es el email, no hay un campo de username separado en el proyecto.
    user = db.query(User).filter(User.email == form_data.username).first()

    # Importante: verify_password (bcrypt, deliberadamente lento) corre
    # SIEMPRE, exista o no el usuario -- comparando contra _DUMMY_HASH si
    # no existe. Si solo llamáramos a verify_password cuando el usuario
    # existe, un email inexistente respondería mucho más rápido que uno
    # real con password incorrecta, y esa diferencia de tiempo le permite
    # a un atacante enumerar qué emails están registrados sin que el
    # cuerpo de la respuesta (siempre genérico) se lo diga.
    hashed = user.hashed_password if user is not None else _DUMMY_HASH
    password_matches = verify_password(form_data.password, hashed)

    if user is None or not password_matches:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email o contraseña incorrectos",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Token(access_token=create_access_token(user.id))
