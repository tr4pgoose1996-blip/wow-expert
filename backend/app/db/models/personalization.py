"""Personalization ORM models.

Hermes learns the player over time: a durable profile of preferences and goals,
plus persisted conversations so context survives across sessions. Both are keyed
to the owning ``User``.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import (
    Base,
    JSONBType,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

if TYPE_CHECKING:
    from app.db.models.user import User


class PlayerProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable, learned preferences for a player."""

    __tablename__ = "player_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )

    favorite_class: Mapped[str | None] = mapped_column(String(40), nullable=True)
    favorite_spec: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Blizzard specialization id of the most-played spec (for fast lookups).
    favorite_spec_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    favorite_content: Mapped[list[str]] = mapped_column(
        JSONBType, nullable=False, default=list
    )
    current_goals: Mapped[list[str]] = mapped_column(
        JSONBType, nullable=False, default=list
    )
    current_farms: Mapped[list[str]] = mapped_column(
        JSONBType, nullable=False, default=list
    )
    # free-form playstyle tags, e.g. ["min-maxer", "casual", "mythic_plus"]
    preferred_playstyle: Mapped[list[str]] = mapped_column(
        JSONBType, nullable=False, default=list
    )
    # Confidence 0..1 that the learned profile is accurate (rises with signals).
    confidence: Mapped[float] = mapped_column(nullable=False, default=0.0)
    # Last inferred facts, for transparency/debuggability.
    last_signals: Mapped[dict[str, Any]] = mapped_column(
        JSONBType, nullable=False, default=dict
    )

    user: Mapped[User] = relationship(back_populates="profile")  # type: ignore[valid-type]


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A remembered conversation thread with a player."""

    __tablename__ = "conversations"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Optional grouping, e.g. "rotation", "gear", "collecting".
    topic: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)

    messages: Mapped[list[ConversationMessage]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ConversationMessage.created_at",
    )
    user: Mapped[User] = relationship(back_populates="conversations")  # type: ignore[valid-type]


class ConversationMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One turn (user or assistant) inside a remembered conversation."""

    __tablename__ = "conversation_messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Structured metadata (e.g. which module answered, referenced entities).
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONBType, nullable=False, default=dict
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
