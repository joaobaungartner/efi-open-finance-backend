from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.efi_pix_payment_model import EfiPixPaymentModel


class PixIdempotencyConflict(ValueError):
    pass


class InvalidPixAuthorizationResponse(ValueError):
    """Campos de autorização inválidos antes de qualquer escrita."""


class EfiPixPaymentRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_idempotency_key(
        self, key: str
    ) -> EfiPixPaymentModel | None:
        return self.session.scalar(
            select(EfiPixPaymentModel).where(
                EfiPixPaymentModel.idempotency_key == key
            )
        )

    def reserve(
        self,
        *,
        idempotency_key: str,
        request_hash: str,
        amount: Decimal,
        origin_institution: str,
    ) -> tuple[EfiPixPaymentModel, bool]:
        """Reserva uma solicitação sem iniciar transferência.

        Retorna (pagamento, criado_agora).
        """

        if not 1 <= len(idempotency_key) <= 128:
            raise ValueError("Chave de idempotência inválida")

        if (
            len(request_hash) != 64
            or any(c not in "0123456789abcdef" for c in request_hash)
        ):
            raise ValueError("Hash da solicitação inválido")

        if not 1 <= len(origin_institution) <= 120:
            raise ValueError("Instituição de origem inválida")

        if (
            not isinstance(amount, Decimal)
            or not amount.is_finite()
            or amount <= 0
            or amount.quantize(Decimal("0.01")) != amount
        ):
            raise ValueError("Valor monetário inválido")

        payment = EfiPixPaymentModel(
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            id_proprio=f"v7-{uuid4().hex}",
            amount=amount,
            origin_institution=origin_institution,
            status="draft",
        )

        self.session.add(payment)

        try:
            self.session.commit()
            self.session.refresh(payment)
            return payment, True

        except IntegrityError:
            self.session.rollback()

            existing = self.get_by_idempotency_key(
                idempotency_key
            )

            if existing is None:
                raise

            if (
                existing.request_hash != request_hash
                or existing.amount != amount
                or existing.origin_institution != origin_institution
            ):
                raise PixIdempotencyConflict(
                    "Chave de idempotência utilizada "
                    "com dados diferentes"
                )

            return existing, False


    def claim_for_submission(self, key: str) -> bool:
        """Permite apenas uma transição atômica draft -> submitting.

        True significa que esta operação ganhou o direito de enviar.
        False significa que já foi reivindicada ou não existe.
        """
        result = self.session.execute(
            update(EfiPixPaymentModel)
            .where(
                EfiPixPaymentModel.idempotency_key == key,
                EfiPixPaymentModel.status == "draft",
                EfiPixPaymentModel.id_proprio.is_not(None),
            )
            .values(status="submitting")
        )
        self.session.commit()
        return result.rowcount == 1

    def mark_awaiting_authorization(
        self,
        key: str,
        *,
        efi_payment_id: str,
        redirect_uri: str,
    ) -> bool:
        """Registra resposta da Efí; não confirma liquidação Pix."""
        if not isinstance(efi_payment_id, str) or not 1 <= len(efi_payment_id) <= 300:
            raise InvalidPixAuthorizationResponse("Identificador Efí inválido")

        if (
            not isinstance(redirect_uri, str)
            or not redirect_uri.startswith("https://")
            or len(redirect_uri) > 4096
        ):
            raise InvalidPixAuthorizationResponse("URL de autorização inválida")

        result = self.session.execute(
            update(EfiPixPaymentModel)
            .where(
                EfiPixPaymentModel.idempotency_key == key,
                EfiPixPaymentModel.status == "submitting",
            )
            .values(
                status="awaiting_authorization",
                efi_payment_id=efi_payment_id,
                redirect_uri=redirect_uri,
            )
        )
        self.session.commit()
        return result.rowcount == 1

    def mark_unknown(
        self,
        key: str,
        *,
        efi_http_status: int | None = None,
        efi_error_code: str | None = None,
    ) -> bool:
        """Marca resultado incerto; nunca autoriza reenvio."""
        result = self.session.execute(
            update(EfiPixPaymentModel)
            .where(
                EfiPixPaymentModel.idempotency_key == key,
                EfiPixPaymentModel.status == "submitting",
            )
            .values(
                status="unknown",
                efi_http_status=efi_http_status,
                efi_error_code=efi_error_code,
            )
        )
        self.session.commit()
        return result.rowcount == 1
