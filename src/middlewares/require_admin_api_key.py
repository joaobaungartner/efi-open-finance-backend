import secrets

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from config.settings import get_settings

admin_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


def require_admin_api_key(key: str | None = Security(admin_header)):
    expected = get_settings().admin_api_key.get_secret_value()
    if not key or not secrets.compare_digest(key, expected):
        raise HTTPException(status_code=401, detail="Não autorizado")
