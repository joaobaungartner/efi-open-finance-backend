from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database.database import Base


class EfiWebhookEventModel(Base):
    __tablename__ = "efi_webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Hash do corpo canônico: retransmissões idênticas são idempotentes.
    payload_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    identifier: Mapped[str] = mapped_column(String(300), index=True)
    status: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(80))
    amount: Mapped[str | None] = mapped_column(String(50), nullable=True)
    end_to_end_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
