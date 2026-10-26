from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, SmallInteger, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import FeedbackReason, db_enum
from app.models.user import User


class AIFeedback(Base, TimestampMixin):
    """An analyst's verdict on an AI investigation: 👍 useful or 👎 incorrect (with a reason).

    One row per person per investigation; changing your mind updates it. Together with the stored evidence this is a
    labelled dataset — the useful-rate by engine, hypothesis and writer is how a prompt or model change is judged.
    """

    __tablename__ = "ai_feedback"
    __table_args__ = (
        UniqueConstraint("investigation_id", "user_id"),
        CheckConstraint("rating IN (-1, 1)", name="rating_values"),
        # "Incorrect" must say why, or the feedback cannot be acted on.
        CheckConstraint("rating = 1 OR reason IS NOT NULL", name="negative_needs_reason"),
        Index("ix_ai_feedback_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    investigation_id: Mapped[int] = mapped_column(ForeignKey("ai_investigations.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    rating: Mapped[int] = mapped_column(SmallInteger)  # 1 = useful, -1 = incorrect
    reason: Mapped[FeedbackReason | None] = mapped_column(db_enum(FeedbackReason, "feedback_reason"))
    comment: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(lazy="joined")
