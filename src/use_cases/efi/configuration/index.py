import httpx
from fastapi import APIRouter, Depends, HTTPException

from middlewares.require_admin_api_key import require_admin_api_key
from services.efi_api_client import EfiConfigurationError, EfiOpenFinanceClient

router = APIRouter(prefix="/internal/efi", tags=["Efí administration"])


@router.get("/config", dependencies=[Depends(require_admin_api_key)])
def get_efi_config():
    try:
        result = EfiOpenFinanceClient().get_config()
    except (httpx.HTTPError, EfiConfigurationError) as exc:
        raise HTTPException(status_code=502, detail="Falha ao consultar configuração Efí") from exc
    if isinstance(result.get("webhookSecurity"), dict):
        result["webhookSecurity"].pop("hash", None)
    return result
