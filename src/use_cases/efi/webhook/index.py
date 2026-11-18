import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from database.database import get_db
from repositories.efi_event_repository import EfiEventRepository
from use_cases.efi.webhook.webhook_use_case import ReceiveEfiWebhookUseCase

router = APIRouter(tags=["Efí callbacks"])


@router.get("/webhooks/efi")
def webhook_reachable():
    # Somente para verificar disponibilidade do endpoint (não expõe credenciais).
    return {"status": "available"}


@router.post("/webhooks/efi")
async def receive_efi_webhook(
    request: Request,
    hmac: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    # Faz leitura por partes para interromper imediatamente payloads excessivos.
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 65536:
            raise HTTPException(status_code=413, detail="Payload excede limite")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="JSON inválido") from exc

    return ReceiveEfiWebhookUseCase(EfiEventRepository(db)).execute(hmac, payload)
