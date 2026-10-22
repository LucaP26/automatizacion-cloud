from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    # Solo la usa la suite de tests (tests/conftest.py). Opcional para que
    # los containers de producción (API, worker) no tengan que inventar una
    # URL de una base que nunca van a tocar.
    test_database_url: str | None = None
    execution_timeout_seconds: int = 30
    execution_memory_limit: str = "128m"
    execution_cpu_limit: float = 0.5  # fraccion de un core
    # Directorio donde el executor escribe el script de cada ejecución antes
    # de montarlo en el container sandbox. None = el temp del sistema (lo
    # correcto corriendo la API directo en el host). Corriendo la API adentro
    # de un container que usa el Docker del host (docker-compose), TIENE que
    # ser un path montado con el mismo nombre en el host y en el container:
    # el daemon resuelve el bind mount del sandbox en el filesystem del host,
    # no en el del container de la API (ver README, Fase 5).
    execution_workdir: str | None = None
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

    # Cada cuánto el worker relee la tabla `automations` para reconciliar
    # los jobs agendados (altas, bajas, cambios de cron). Es la latencia
    # máxima entre editar una automatización y que el scheduler se entere.
    scheduler_sync_interval_seconds: int = 30

    # extra="ignore": el .env también tiene variables que no son de la app
    # sino de docker compose (POSTGRES_PASSWORD, API_PORT...). Sin esto,
    # pydantic-settings rechaza cualquier clave del .env que no sea un campo.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
