"""Battle.net integration: linked accounts, sync jobs, and character snapshots.

Revision ID: 0002_blizzard_integration
Revises: 0001_initial
Create Date: 2026-08-02
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_blizzard_integration"
down_revision: str | None = "0001_initial"
branch_labels = None
depends_on = None

_EMPTY_OBJECT = sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "blizzard_accounts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("battlenet_id", sa.BigInteger(), nullable=False),
        sa.Column("battletag", sa.String(64), nullable=True),
        sa.Column("region", sa.String(4), nullable=False, server_default="us"),
        # Tokens are encrypted at rest; the column holds Fernet ciphertext.
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", sa.String(255), nullable=False, server_default=""),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "user_id", "region", name="uq_blizzard_accounts_user_region"
        ),
        sa.UniqueConstraint(
            "battlenet_id",
            "region",
            name="uq_blizzard_accounts_battlenet_region",
        ),
    )
    op.create_index(
        "ix_blizzard_accounts_user_id", "blizzard_accounts", ["user_id"]
    )
    # Drives the weekly sweep's "who is stale?" query.
    op.create_index(
        "ix_blizzard_accounts_sync_due",
        "blizzard_accounts",
        ["is_active", "last_sync_at"],
    )

    op.create_table(
        "sync_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("blizzard_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "character_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("characters.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="pending"
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "characters_synced",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "characters_failed",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(),
            nullable=False,
            server_default=_EMPTY_OBJECT,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_sync_jobs_account_id", "sync_jobs", ["account_id"])
    op.create_index(
        "ix_sync_jobs_account_created",
        "sync_jobs",
        ["account_id", sa.text("created_at DESC")],
    )
    # Makes the "is a sync already running?" guard an index lookup.
    op.create_index(
        "ix_sync_jobs_running",
        "sync_jobs",
        ["account_id"],
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )

    op.create_table(
        "character_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "character_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("characters.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("blizzard_character_id", sa.BigInteger(), nullable=True),
        sa.Column("guild_name", sa.String(64), nullable=True),
        sa.Column("race", sa.String(40), nullable=True),
        sa.Column(
            "achievement_points",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        *[
            sa.Column(
                column,
                postgresql.JSONB(),
                nullable=False,
                server_default=_EMPTY_OBJECT,
            )
            for column in (
                "equipment",
                "talents",
                "professions",
                "achievements",
                "reputations",
                "mounts",
                "pets",
            )
        ],
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "synced_scopes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_character_snapshots_character_id",
        "character_snapshots",
        ["character_id"],
    )


def downgrade() -> None:
    op.drop_table("character_snapshots")
    op.drop_table("sync_jobs")
    op.drop_table("blizzard_accounts")
