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

    # Olist ERP (API v3, somente leitura). Chaves do aplicativo criado no Olist.
    olist_client_id: str = Field(default="", repr=False)
    olist_client_secret: str = Field(default="", repr=False)
    olist_redirect_uri: str = "http://localhost:8000/integracoes/olist/retorno"
    # De quantos em quantos minutos o painel busca novidades no Olist.
    olist_sync_minutes: int = 10
    # Na primeira conexão, quantos dias de pedidos e notas para trás importar.
    olist_initial_days: int = 15

    # Mercado Livre (API oficial). Chaves do aplicativo criado no DevCenter.
    ml_client_id: str = Field(default="", repr=False)
    ml_client_secret: str = Field(default="", repr=False)
    # O Mercado Livre exige HTTPS público: a página de repasse (GitHub Pages)
    # devolve o login para o painel local. Tem que ser IGUAL à cadastrada no app.
    ml_redirect_uri: str = "https://alyssonov.github.io/painel-retorno/mercadolivre/"
    ml_sync_minutes: int = 5

    # E-mail para os relatórios (Gmail: smtp.gmail.com, porta 587, "senha de app").
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = Field(default="", repr=False)
    email_from: str = ""  # vazio = usa o SMTP_USER

    anthropic_api_key: str = Field(default="", repr=False)
    ai_daily_budget_brl: float = 10.0
    # Cotação usada só para ESTIMAR o gasto em reais (a cobrança é em dólar).
    usd_brl_rate: float = 5.50
    # De quantos em quantos minutos o Respondedor de perguntas olha a fila.
    agent_questions_minutes: int = 3

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
    def olist_configured(self) -> bool:
        """True quando as chaves do aplicativo do Olist estão no .env."""
        return bool(self.olist_client_id and self.olist_client_secret)

    @property
    def ml_configured(self) -> bool:
        """True quando as chaves do aplicativo do Mercado Livre estão no .env."""
        return bool(self.ml_client_id and self.ml_client_secret)

    @property
    def email_configured(self) -> bool:
        """True quando o e-mail que envia os relatórios está configurado no .env."""
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)

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
