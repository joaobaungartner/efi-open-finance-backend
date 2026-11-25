from functools import lru_cache
from os import getenv
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_ignore_empty=True, hide_input_in_errors=True, extra="ignore"
    )

    app_env: str = "dev"
    dry_run: bool = False
    client_url: str = "http://localhost:5173"
    database_url: str = "sqlite:///./efi_dev.sqlite3"

    # Acesso às rotas internas (não é a credencial bancária).
    admin_api_key: SecretStr = Field(min_length=24)

    # Efí chama este valor de 'hmac', mas a documentação envia um segredo estático
    # no query param ?hmac=... (não uma assinatura calculada do corpo).
    efi_webhook_hash: SecretStr = Field(min_length=32)
    efi_environment: str = "homologation"  # homologation | production
    efi_client_id: SecretStr | None = None
    efi_client_secret: SecretStr | None = None
    efi_certificate_path: Path | None = None
    efi_certificate_password: SecretStr | None = None
    public_base_url: str | None = None

    @model_validator(mode="after")
    def validate_environment(self):
        if self.dry_run and self.app_env not in ("dev", "test"):
            raise ValueError("DRY_RUN requer APP_ENV=dev ou test")
        if self.app_env not in ("dev", "test", "prod"):
            raise ValueError("APP_ENV deve ser dev, test ou prod")
        if self.efi_environment not in ("homologation", "production"):
            raise ValueError("EFI_ENVIRONMENT deve ser homologation ou production")
        if self.app_env == "prod":
            if self.database_url.startswith("sqlite"):
                raise ValueError("Use PostgreSQL em APP_ENV=prod")
            if not self.client_url.startswith("https://"):
                raise ValueError("CLIENT_URL precisa ser HTTPS em APP_ENV=prod")
            if not self.public_base_url or not self.public_base_url.startswith("https://"):
                raise ValueError("PUBLIC_BASE_URL precisa ser HTTPS em APP_ENV=prod")
        return self

    @property
    def efi_api_base(self) -> str:
        return (
            "https://openfinance.api.efipay.com.br"
            if self.efi_environment == "production"
            else "https://openfinance-h.api.efipay.com.br"
        )


@lru_cache
def get_settings() -> Settings:
    if getenv("EFI_LOCAL_SANDBOX") == "1":
        return Settings(_env_file=None)
    return Settings()
