"""User and authentication schemas."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.config import settings
from app.db.models.enums import UserRole
from app.schemas.common import ORMModel

USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{3,32}$")


class UserBase(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=32)
    display_name: str | None = Field(default=None, max_length=64)

    @field_validator("username")
    @classmethod
    def _validate_username(cls, value: str) -> str:
        if not USERNAME_PATTERN.match(value):
            raise ValueError(
                "Username may contain only letters, numbers, hyphens, "
                "and underscores."
            )
        return value.lower()


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=72)

    @field_validator("password")
    @classmethod
    def _validate_password_strength(cls, value: str) -> str:
        if len(value) < settings.PASSWORD_MIN_LENGTH:
            raise ValueError(
                f"Password must be at least {settings.PASSWORD_MIN_LENGTH} "
                "characters."
            )
        checks = (
            (any(c.islower() for c in value), "a lowercase letter"),
            (any(c.isupper() for c in value), "an uppercase letter"),
            (any(c.isdigit() for c in value), "a digit"),
        )
        missing = [label for ok, label in checks if not ok]
        if missing:
            raise ValueError(f"Password must contain {', '.join(missing)}.")
        return value


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=64)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=8, max_length=72)

    @field_validator("new_password")
    @classmethod
    def _validate(cls, value: str) -> str:
        return UserCreate._validate_password_strength(value)


class UserRead(ORMModel):
    id: uuid.UUID
    email: EmailStr
    username: str
    display_name: str | None
    role: UserRole
    is_active: bool
    is_verified: bool
    created_at: datetime


class LoginRequest(BaseModel):
    """Login accepts either the email address or the username."""

    identifier: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=72)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)
