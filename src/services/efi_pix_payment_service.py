import hashlib
import json
import re
from decimal import Decimal

import httpx

from entities.efi_pix_request import PixByKeyRequest
from repositories.efi_pix_payment_repository import (
    EfiPixPaymentRepository,
    InvalidPixAuthorizationResponse,
)
from services.efi_api_client import (
    EfiConfigurationError,
    EfiOpenFinanceClient,
    EfiUnexpectedResponse,
)


def _http_error_code(status: int) -> str:
    if status in (400, 401, 403, 422):
        return f"efi_http_{status}"
    if status == 429:
        return "efi_rate_limited"
    if 500 <= status <= 599:
        return "efi_server_error"
    return "efi_http_unexpected"


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
            "id_proprio": payment.id_proprio,
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

        request = PixByKeyRequest.model_validate(payload)

        if request.pagamento.idProprio is not None:
            raise ValueError(
                "idProprio deve ser gerado pela plataforma V7"
            )

        payload = request.model_dump(exclude_none=True)
        amount = Decimal(request.pagamento.valor)

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

        payment, created = self.repository.reserve(
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

        if not payment.id_proprio:
            raise RuntimeError(
                "idProprio não persistido. Pix não enviado."
            )

        # O mesmo identificador salvo no banco vai para a Efí.
        payload["pagamento"]["idProprio"] = payment.id_proprio

        # A chave local permanece separada da chave bancária.
        bank_key = hashlib.sha512(
            f"efi-pix-v1:{payment.id_proprio}".encode("ascii")
        ).hexdigest()[:72]
        http_status = None
        try:
            response = self.client.start_pix(
                payload,
                idempotency_key=bank_key,
            )

        except httpx.HTTPStatusError as exc:
            http_status = exc.response.status_code
            error_code = _http_error_code(http_status)
        except EfiUnexpectedResponse as exc:
            http_status = exc.status_code
            error_code = "efi_invalid_response"
        except httpx.TimeoutException:
            error_code = "efi_timeout"
        except httpx.TransportError:
            error_code = "efi_transport_error"
        except httpx.DecodingError:
            error_code = "efi_invalid_response"
        except EfiConfigurationError:
            error_code = "local_configuration_error"
        except ValueError:
            error_code = "local_validation_error"
        except Exception:
            error_code = "submission_unexpected_error"
        else:
            error_code = self._store_authorization(idempotency_key, response)

        # Um erro HTTP por si só não comprova que o Pix não foi criado.
        if error_code is not None:
            try:
                self.repository.mark_unknown(
                    idempotency_key,
                    efi_http_status=http_status,
                    efi_error_code=error_code,
                )
            except Exception:
                self.repository.session.rollback()
                raise

        return self._result(idempotency_key)

    def _store_authorization(self, key: str, response: object) -> str | None:
        if not isinstance(response, dict):
            return "efi_invalid_response"

        try:
            stored = self.repository.mark_awaiting_authorization(
                key,
                efi_payment_id=response.get("identificadorPagamento"),
                redirect_uri=response.get("redirectURI"),
            )
        except InvalidPixAuthorizationResponse:
            return "efi_invalid_response"
        except Exception:
            # Desfaz a transação antes de tentar persistir o diagnóstico.
            self.repository.session.rollback()
            return "local_persistence_error"

        if not stored:
            return "local_persistence_error"
        return None
