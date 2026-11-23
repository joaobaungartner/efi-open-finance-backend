import hashlib
import json
import secrets

from fastapi import HTTPException
from pydantic import ValidationError

from config.settings import get_settings
from entities.efi_event import EfiWebhookDTO
from models.efi_webhook_event_model import EfiWebhookEventModel
from repositories.efi_event_repository import EfiEventRepository


class ReceiveEfiWebhookUseCase:
    def __init__(self, repository: EfiEventRepository):
        self.repository = repository

    def execute(self, received_hash: str | None, payload: dict) -> dict:
        expected = get_settings().efi_webhook_hash.get_secret_value()
        if not received_hash or not secrets.compare_digest(received_hash, expected):
            raise HTTPException(status_code=401, detail="Webhook não autorizado")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="Payload inválido")

        # Verificação de disponibilidade da Efí.
        # O HMAC já foi validado acima.
        # Não cria evento financeiro no banco.
        if not payload:
            return {"received": True, "verification": True}

        try:
            dto = EfiWebhookDTO.model_validate(payload)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="Payload Efí inválido") from exc
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        event = EfiWebhookEventModel(
            payload_hash=fingerprint,
            identifier=dto.identifier,
            status=dto.status,
            kind=dto.tipo,
            amount=dto.valor,
            end_to_end_id=dto.endToEndId,
        )
        inserted = self.repository.save_if_new(event)
        # Status do webhook é informativo, nunca prova definitiva de dinheiro liquidado.
        return {"received": True, "new_event": inserted}
