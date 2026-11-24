from datetime import UTC, date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from database.database import Base


class EfiPixInvestigationModel(Base):
    __tablename__ = "efi_pix_investigations"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(
        ForeignKey("efi_pix_payments.id"), index=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_status: Mapped[str] = mapped_column(String(40))
    result: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(30))
    period_start: Mapped[date] = mapped_column(Date())
    period_end: Mapped[date] = mapped_column(Date())
