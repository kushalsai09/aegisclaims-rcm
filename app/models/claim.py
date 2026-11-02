from datetime import date, datetime, timezone

from decimal import Decimal

from sqlalchemy import Date, DateTime, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    patient_name: Mapped[str] = mapped_column(String(150), nullable=False)
    insurer: Mapped[str] = mapped_column(String(100), nullable=False)
    cpt_code: Mapped[str] = mapped_column(String(20), nullable=False)
    denial_code: Mapped[str] = mapped_column(String(20), nullable=False)
    denial_reason: Mapped[str] = mapped_column(Text, nullable=False)
    auth_number_in_chart: Mapped[str | None] = mapped_column(String(50), nullable=True)
    modifier_attached: Mapped[str | None] = mapped_column(String(20), nullable=True)
    diagnosis_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    eob_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    date_of_service: Mapped[date] = mapped_column(Date, nullable=False, default=date.today)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    suggested_fix: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_citation: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
