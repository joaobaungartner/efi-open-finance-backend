from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.database import Base


class EfiPixPaymentModel(Base):
    __tablename__ = "efi_pix_payments"

    __table_args__ = (
        CheckConstraint(
            "amount > 0",
            name="ck_efi_pix_payments_amount_positive",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    idempotency_key: Mapped[str] = mapped_column(
        String(128), unique=True, index=True, nullable=False
    )

    request_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )

    id_proprio: Mapped[str | None] = mapped_column(
        String(100), unique=True, index=True, nullable=True
    )

    efi_payment_id: Mapped[str | None] = mapped_column(
        String(300), unique=True, index=True, nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(40), default="draft", nullable=False
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False
    )

    origin_institution: Mapped[str] = mapped_column(
        String(120), nullable=False
    )

    redirect_uri: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
