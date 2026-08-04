"""Character profile schemas."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, Field, model_validator

from app.db.models.enums import (
    MAX_CHARACTER_LEVEL,
    CharacterClass,
    ContentFocus,
    Faction,
    Role,
    role_is_valid_for_class,
)
from app.schemas.common import ORMModel

NAME_PATTERN = re.compile(r"^[A-Za-zÀ-ÿ]{2,12}$")
VALID_REGIONS = frozenset({"us", "eu", "kr", "tw", "cn"})


class CharacterBase(BaseModel):
    name: str = Field(min_length=2, max_length=12)
    realm: str = Field(min_length=2, max_length=64)
    region: str = Field(default="us", min_length=2, max_length=4)
    faction: Faction
    character_class: CharacterClass
    primary_role: Role
    specialization: str | None = Field(default=None, max_length=32)
    level: int = Field(default=1, ge=1, le=MAX_CHARACTER_LEVEL)
    item_level: int = Field(default=0, ge=0, le=1000)
    content_focus: ContentFocus = ContentFocus.LEVELING
    goals: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if not NAME_PATTERN.match(self.name):
            raise ValueError(
                "Character name must be 2-12 letters with no spaces or digits."
            )
        self.name = self.name.capitalize()

        self.region = self.region.lower()
        if self.region not in VALID_REGIONS:
            raise ValueError(
                f"Region must be one of: {', '.join(sorted(VALID_REGIONS))}."
            )

        self.realm = self.realm.strip()

        if not role_is_valid_for_class(self.character_class, self.primary_role):
            raise ValueError(
                f"A {self.character_class.value} cannot fill the "
                f"{self.primary_role.value} role."
            )
        return self


class CharacterCreate(CharacterBase):
    pass


class CharacterUpdate(BaseModel):
    """Partial update. Class and role are revalidated together when either
    changes, so a character can never be left in an illegal combination."""

    specialization: str | None = Field(default=None, max_length=32)
    primary_role: Role | None = None
    character_class: CharacterClass | None = None
    level: int | None = Field(default=None, ge=1, le=MAX_CHARACTER_LEVEL)
    item_level: int | None = Field(default=None, ge=0, le=1000)
    content_focus: ContentFocus | None = None
    goals: str | None = Field(default=None, max_length=1000)


class CharacterRead(ORMModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    realm: str
    region: str
    faction: Faction
    character_class: CharacterClass
    primary_role: Role
    specialization: str | None
    level: int
    item_level: int
    content_focus: ContentFocus
    goals: str | None
    external_data: dict[str, Any]
    created_at: datetime
    updated_at: datetime
