"""Configuração do sistema, lida do arquivo .env (e de variáveis de ambiente).

Todos os segredos ficam no .env — nunca no código. Veja .env.example.
"""

from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurações do painel. Os nomes batem com as variáveis do .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    # Modo simulação é o padrão: só vira "false" por decisão explícita no .env.
    dry_run: bool = True
    timezone: str = "America/Sao_Paulo"

    secret_key: str = Field(default="", repr=False)
    encryption_key: str = Field(default="", repr=False)
    session_cookie_secure: bool = False
    session_max_age_hours: int = 12
    login_max_attempts: int = 5
    login_lock_minutes: int = 15

    database_url: str = "sqlite:///./data/painel.db"

    anthropic_api_key: str = Field(default="", repr=False)
    ai_daily_budget_brl: float = 10.0

    @field_validator("secret_key")
    @classmethod
    def _secret_key_forte(cls, value: str) -> str:
        # Sem uma chave forte, qualquer um poderia forjar o cookie de sessão.
        if value and len(value) < 32:
            raise ValueError("SECRET_KEY precisa ter pelo menos 32 caracteres (use `make keys`).")
        return value

    @property
    def tz(self) -> ZoneInfo:
        """Fuso horário da loja (usado para definir o que é 'hoje')."""
        return ZoneInfo(self.timezone)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def check_ready(self) -> None:
        """Interrompe a inicialização se faltar alguma configuração obrigatória."""
        missing = [name for name in ("secret_key", "encryption_key") if not getattr(self, name)]
        if missing:
            nomes = ", ".join(n.upper() for n in missing)
            raise RuntimeError(f"Faltam no .env: {nomes}. Rode `make setup` para gerar.")
        if self.is_production and not self.session_cookie_secure:
            raise RuntimeError("Em produção, SESSION_COOKIE_SECURE precisa ser true (HTTPS).")


@lru_cache
def get_settings() -> Settings:
    """Retorna as configurações (lidas uma única vez)."""
    return Settings()
