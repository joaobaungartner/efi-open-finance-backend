import hashlib
import json
import re
from decimal import Decimal

from repositories.efi_pix_payment_repository import EfiPixPaymentRepository
from services.efi_api_client import EfiConfigurationError, EfiOpenFinanceClient


class EfiPixPaymentService:
    def __init__(
        self,
        repository: EfiPixPaymentRepository,
        client: EfiOpenFinanceClient,
    ):
        self.repository = repository
        self.client = client

    def _result(self, key: str) -> dict:
        payment = self.repository.get_by_idempotency_key(key)

        if payment is None:
            raise RuntimeError("Pagamento não encontrado")

        return {
            "id": payment.id,
            "status": payment.status,
            "efi_payment_id": payment.efi_payment_id,
            "redirect_uri": payment.redirect_uri,
        }

    def submit(
        self,
        *,
        idempotency_key: str,
        origin_institution: str,
        payload: dict,
    ) -> dict:
        # Dupla proteção: serviço e cliente HTTP.
        if self.client.settings.efi_environment != "homologation":
            raise EfiConfigurationError(
                "Iniciação Pix permitida apenas em Homologação"
            )

        if (
            not isinstance(idempotency_key, str)
            or re.fullmatch(
                r"[A-Za-z0-9._:-]{1,128}", idempotency_key
            ) is None
        ):
            raise ValueError("Chave de idempotência inválida")

        if (
            not isinstance(origin_institution, str)
            or not origin_institution.strip()
            or len(origin_institution) > 120
        ):
            raise ValueError("Instituição de origem inválida")

        if not isinstance(payload, dict) or not all(
            isinstance(payload.get(field), dict)
            for field in ("pagador", "favorecido", "pagamento")
        ):
            raise ValueError("Payload Pix inválido")

        value = payload["pagamento"].get("valor")

        if (
            not isinstance(value, str)
            or re.fullmatch(
                r"(?:0|[1-9][0-9]{0,15})\.[0-9]{2}",
                value,
            ) is None
        ):
            raise ValueError("Valor Pix inválido")

        amount = Decimal(value)
        if amount <= 0:
            raise ValueError("Valor Pix deve ser positivo")

        # O hash representa os dados completos da solicitação.
        canonical = json.dumps(
            {
                "origin_institution": origin_institution,
                "payload": payload,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )

        request_hash = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()

        _, created = self.repository.reserve(
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            amount=amount,
            origin_institution=origin_institution,
        )

        # Uma solicitação existente nunca provoca novo POST bancário.
        if not created:
            return self._result(idempotency_key)

        # Transição atômica: draft -> submitting.
        if not self.repository.claim_for_submission(idempotency_key):
            return self._result(idempotency_key)

        try:
            response = self.client.start_pix(
                payload,
                idempotency_key=idempotency_key,
            )

            if not isinstance(response, dict):
                raise ValueError("Resposta Efí inválida")

            identifier = response.get("identificadorPagamento")
            redirect = response.get("redirectURI")

            if not self.repository.mark_awaiting_authorization(
                idempotency_key,
                efi_payment_id=identifier,
                redirect_uri=redirect,
            ):
                raise RuntimeError(
                    "Não foi possível registrar a resposta Efí"
                )

        except Exception:
            # Pode ter ocorrido envio sem resposta conclusiva.
            # Nunca fazemos retry automático do POST.
            self.repository.session.rollback()
            self.repository.mark_unknown(idempotency_key)

        return self._result(idempotency_key)
