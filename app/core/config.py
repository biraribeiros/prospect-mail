from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_SECRET = "troque-por-um-valor-longo-e-aleatorio"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    app_env: str = "dev"
    database_url: str = "postgresql+psycopg://prospector:prospector@localhost:5432/prospector"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = INSECURE_SECRET
    fernet_keys: str = ""
    session_https_only: bool = False
    dry_run_raw: str = Field(default="true", validation_alias="DRY_RUN")
    admin_email: str = ""
    admin_password: str = ""
    retention_days: int = 730
    smtp_host: str = "localhost"
    smtp_port: int = 1025

    @property
    def dry_run(self) -> bool:
        """Regra 3: só o valor exato 'false' envia de verdade. Qualquer outro = simulação."""
        return self.dry_run_raw.strip().lower() != "false"

    def check_production_safety(self) -> None:
        if self.app_env != "dev" and self.secret_key == INSECURE_SECRET:
            raise RuntimeError("SECRET_KEY padrão não pode ser usada fora de dev.")


@lru_cache
def get_settings() -> Settings:
    return Settings()
