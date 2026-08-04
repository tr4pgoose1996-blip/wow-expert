"""Character profile ORM model."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.enums import (
    MAX_CHARACTER_LEVEL,
    CharacterClass,
    ContentFocus,
    Faction,
    Role,
)
from app.db.session import (
    Base,
    JSONBType,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    UUIDType,
)

if TYPE_CHECKING:
    from app.db.models.blizzard import CharacterSnapshot
    from app.db.models.user import User


class Character(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A WoW character belonging to a user, used to personalise coaching."""

    __tablename__ = "characters"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "name", "realm", name="owner_name_realm"
        ),
        CheckConstraint(
            f"level >= 1 AND level <= {MAX_CHARACTER_LEVEL}",
            name="level_range",
        ),
        CheckConstraint("item_level >= 0", name="item_level_non_negative"),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(12), nullable=False)
    realm: Mapped[str] = mapped_column(String(64), nullable=False)
    region: Mapped[str] = mapped_column(
        String(4), nullable=False, default="us", server_default="us"
    )

    faction: Mapped[Faction] = mapped_column(
        Enum(Faction, name="faction", native_enum=False, length=16),
        nullable=False,
    )
    character_class: Mapped[CharacterClass] = mapped_column(
        Enum(CharacterClass, name="character_class", native_enum=False, length=24),
        nullable=False,
    )
    primary_role: Mapped[Role] = mapped_column(
        Enum(Role, name="role", native_enum=False, length=16), nullable=False
    )
    specialization: Mapped[str | None] = mapped_column(String(32), nullable=True)

    level: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    item_level: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    content_focus: Mapped[ContentFocus] = mapped_column(
        Enum(ContentFocus, name="content_focus", native_enum=False, length=24),
        nullable=False,
        default=ContentFocus.LEVELING,
        server_default=ContentFocus.LEVELING.value,
    )
    goals: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Free-form snapshot of externally-sourced data (Blizzard API, logs).
    # Kept schemaless so new providers do not require a migration.
    external_data: Mapped[dict] = mapped_column(
        JSONBType, nullable=False, default=dict
    )

    owner: Mapped[User] = relationship(back_populates="characters")
    snapshot: Mapped[CharacterSnapshot | None] = relationship(
        back_populates="character",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )

    @property
    def slug(self) -> str:
        """Canonical ``region-realm-name`` identifier, lowercased."""
        return f"{self.region}-{self.realm}-{self.name}".lower().replace(" ", "-")

    def __repr__(self) -> str:
        return f"<Character id={self.id} name={self.name!r} realm={self.realm!r}>"
