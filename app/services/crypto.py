import json

from cryptography.fernet import Fernet

from app.config import settings

# Se construye una sola vez, al importar el modulo (que pasa al arrancar la
# app, via routers/executor). Si SECRET_KEY no es una clave Fernet valida,
# esto explota en el arranque -- fail fast -- en vez de recien en el primer
# request que use secretos.
_fernet = Fernet(settings.secret_key.encode())


def encrypt_secrets(secrets: dict[str, str] | None) -> bytes | None:
    """Encripta el dict de env vars de una automatización para guardarlo en la DB.

    Nunca se guarda en texto plano: si alguien lee la tabla `automations`
    directo (un dump, un backup, un acceso no autorizado a la base), lo que
    ve es un blob encriptado, no las credenciales reales.
    """
    if not secrets:
        return None
    return _fernet.encrypt(json.dumps(secrets).encode("utf-8"))


def decrypt_secrets(encrypted: bytes | None) -> dict[str, str]:
    """Desencripta los secretos. Se llama solo al momento de inyectarlos como
    env vars del container -- nunca se expone el resultado en una respuesta
    de la API (ver schemas/automation.py: no existe en AutomationRead).
    """
    if not encrypted:
        return {}
    return json.loads(_fernet.decrypt(encrypted).decode("utf-8"))
