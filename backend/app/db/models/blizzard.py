"""Battle.net account link and character synchronization ORM models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.enums import SyncScope, SyncStatus
from app.db.session import (
    Base,
    JSONBType,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    UUIDType,
)

if TYPE_CHECKING:
    from app.db.models.character import Character
    from app.db.models.user import User


class BlizzardAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user's linked Battle.net account and its OAuth grant.

    Tokens are stored encrypted (see :mod:`app.core.crypto`). The refresh
    token is the durable part of the grant; the access token is short-lived
    and refreshed transparently.
    """

    __tablename__ = "blizzard_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "region", name="user_region"),
        UniqueConstraint(
            "battlenet_id", "region", name="battlenet_id_region"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    battlenet_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    battletag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    region: Mapped[str] = mapped_column(
        String(4), nullable=False, default="us", server_default="us"
    )

    # Encrypted at rest; never expose these through a schema.
    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scopes: Mapped[str] = mapped_column(
        String(256), nullable=False, default="", server_default=""
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    sync_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    user: Mapped[User] = relationship(back_populates="blizzard_accounts")
    sync_jobs: Mapped[list[SyncJob]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )

    @property
    def token_is_expired(self) -> bool:
        """True when the access token is missing or within 60s of expiry."""
        if self.token_expires_at is None:
            return True
        expires_at = self.token_expires_at
        # Some drivers return naive datetimes for TIMESTAMP columns; treat
        # those as UTC rather than raising on the comparison.
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return datetime.now(UTC) >= expires_at - timedelta(seconds=60)

    @property
    def has_profile_scope(self) -> bool:
        return "wow.profile" in self.scopes.split()

    def is_stale(self, hours: int) -> bool:
        """True when the account has not synced within the given window."""
        if self.last_sync_at is None:
            return True
        age = datetime.now(UTC) - self.last_sync_at
        return age >= timedelta(hours=hours)

    def __repr__(self) -> str:
        return (
            f"<BlizzardAccount id={self.id} battletag={self.battletag!r} "
            f"region={self.region!r}>"
        )


class SyncJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An audit record of one synchronization run.

    Persisting these gives operators a history to debug against and lets the
    API report progress on long-running imports.
    """

    __tablename__ = "sync_jobs"

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        ForeignKey("blizzard_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    character_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType,
        ForeignKey("characters.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    scope: Mapped[SyncScope] = mapped_column(
        Enum(SyncScope, name="sync_scope", native_enum=False, length=24),
        nullable=False,
    )
    status: Mapped[SyncStatus] = mapped_column(
        Enum(SyncStatus, name="sync_status", native_enum=False, length=16),
        nullable=False,
        default=SyncStatus.PENDING,
        server_default=SyncStatus.PENDING.value,
        index=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    characters_synced: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    characters_failed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    details: Mapped[dict] = mapped_column(
        JSONBType, nullable=False, default=dict
    )

    account: Mapped[BlizzardAccount] = relationship(back_populates="sync_jobs")

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    def __repr__(self) -> str:
        return f"<SyncJob id={self.id} scope={self.scope} status={self.status}>"


class CharacterSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Imported Blizzard data for one character.

    Kept in a separate table from :class:`Character` so that user-authored
    profile fields (goals, focus) are never overwritten by an import, and so
    the large JSONBType documents do not weigh down every character query.
    """

    __tablename__ = "character_snapshots"
    __table_args__ = (
        UniqueConstraint("character_id", name="one_snapshot_per_character"),
    )

    character_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType,
        ForeignKey("characters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    blizzard_character_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )
    guild_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    race: Mapped[str | None] = mapped_column(String(32), nullable=True)
    achievement_points: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # One JSONBType column per imported domain. Schemaless by design: Blizzard
    # reshapes these documents every expansion, and a migration per change
    # would be unsustainable.
    equipment: Mapped[dict] = mapped_column(
        JSONBType, nullable=False, default=dict
    )
    talents: Mapped[dict] = mapped_column(
        JSONBType, nullable=False, default=dict
    )
    professions: Mapped[dict] = mapped_column(
        JSONBType, nullable=False, default=dict
    )
    achievements: Mapped[dict] = mapped_column(
        JSONBType, nullable=False, default=dict
    )
    reputations: Mapped[dict] = mapped_column(
        JSONBType, nullable=False, default=dict
    )
    mounts: Mapped[dict] = mapped_column(
        JSONBType, nullable=False, default=dict
    )
    pets: Mapped[dict] = mapped_column(
        JSONBType, nullable=False, default=dict
    )

    synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # Which scopes succeeded in the most recent sync, so a partial import is
    # visible rather than silently looking complete.
    synced_scopes: Mapped[list] = mapped_column(
        JSONBType, nullable=False, default=list
    )

    character: Mapped[Character] = relationship(back_populates="snapshot")

    def __repr__(self) -> str:
        return f"<CharacterSnapshot character_id={self.character_id}>"
