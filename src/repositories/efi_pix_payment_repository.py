from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.efi_pix_payment_model import EfiPixPaymentModel


class PixIdempotencyConflict(ValueError):
    pass


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
