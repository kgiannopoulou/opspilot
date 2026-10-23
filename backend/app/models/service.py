from sqlalchemy import BigInteger, CheckConstraint, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Service(TimestampMixin, Base):
    """An entry in the service catalogue (e.g. payment-service)."""

    __tablename__ = "services"
    __table_args__ = (CheckConstraint("tier BETWEEN 1 AND 4", name="tier_range"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    tier: Mapped[int] = mapped_column(SmallInteger)
    owner_team: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="")
