import httpx
from fastapi import APIRouter, Depends, HTTPException

from middlewares.require_admin_api_key import require_admin_api_key
from services.efi_api_client import EfiConfigurationError, EfiOpenFinanceClient

router = APIRouter(prefix="/internal/efi", tags=["Efí administration"])


@router.get("/participantes", dependencies=[Depends(require_admin_api_key)])
def list_efi_participants():
    try:
        data = EfiOpenFinanceClient().get_participants()
    except (httpx.HTTPError, EfiConfigurationError) as exc:
        raise HTTPException(status_code=502, detail="Falha ao consultar participantes Efí") from exc
    return {
        "participantes": [
            {"identificador": x.get("identificador"), "nome": x.get("nome")}
            for x in data.get("participantes", [])
        ]
    }
