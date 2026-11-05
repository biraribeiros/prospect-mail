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
    # IA (Claude) — usada só como reserva na leitura de assinaturas; sem chave, fica desligada.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-5-5"
    # Estimativa de custo em US$ por milhão de tokens (confira os preços atuais; configurável).
    llm_price_in_per_mtok: float = 0.10
    llm_price_out_per_mtok: float = 0.50
    llm_max_calls_per_scan: int = 200
    data_dir: str = "data"
    mbox_max_mb: int = 500
    # Etapa 2a — empresas
    cnpj_dir: str = "data/cnpj"
    cnpj_max_empresas: int = 50_000
    google_places_api_key: str = ""
    # Estimativa em US$ por 1.000 consultas do Text Search (a tabela do Google muda: confira antes de rodar).
    places_price_per_1000_usd: float = 35.0
    places_max_pages: int = 3
    places_cache_days: int = 30
    places_max_usd_per_run: float = 5.0  # a coleta para sozinha ao atingir este gasto
    crawl_user_agent: str = "ProspectorBot/1.0 (leitura de paginas publicas de contato)"
    crawl_delay_s: float = 1.0
    crawl_timeout_s: float = 10.0
    crawl_max_pages: int = 5

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
