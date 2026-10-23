from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, SmallInteger, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

service_dependencies = Table(
    "service_dependencies",
    Base.metadata,
    Column("service_id", ForeignKey("services.id", ondelete="CASCADE"), primary_key=True),
    Column("depends_on_id", ForeignKey("services.id", ondelete="CASCADE"), primary_key=True),
    CheckConstraint("service_id <> depends_on_id", name="no_self_dependency"),
)


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

    dependencies: Mapped[list["Service"]] = relationship(
        secondary=service_dependencies,
        primaryjoin=lambda: Service.id == service_dependencies.c.service_id,
        secondaryjoin=lambda: Service.id == service_dependencies.c.depends_on_id,
        order_by=lambda: Service.name,
    )
