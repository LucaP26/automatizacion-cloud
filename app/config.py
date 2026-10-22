from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    test_database_url: str
    execution_timeout_seconds: int = 30
    execution_memory_limit: str = "128m"
    execution_cpu_limit: float = 0.5  # fraccion de un core
    secret_key: str  # clave Fernet para encriptar secretos de automatizaciones

    # JWT. Clave DISTINTA de `secret_key`: son dos usos criptográficos
    # distintos (encriptar/desencriptar datos vs. firmar/verificar tokens) y
    # nunca conviene reusar una misma clave para propósitos distintos -- si
    # una se filtra, la otra sigue segura.
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30

    # Webhook para notificar ejecuciones fallidas (p.ej. una Incoming
    # Webhook URL de Slack). Opcional: si no se configura, el notifier no
    # hace nada -- no es un requisito para que el resto de la app funcione.
    notification_webhook_url: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
