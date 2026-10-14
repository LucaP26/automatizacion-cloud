from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    test_database_url: str
    execution_timeout_seconds: int = 30
    execution_memory_limit: str = "128m"
    execution_cpu_limit: float = 0.5  # fraccion de un core
    secret_key: str  # clave Fernet para encriptar secretos de automatizaciones

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
