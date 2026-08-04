"""Shared schema primitives."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    """Base for schemas read directly from ORM instances."""

    model_config = ConfigDict(from_attributes=True)


class PaginationParams(BaseModel):
    """Standard limit/offset pagination inputs."""

    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class Page(BaseModel, Generic[T]):
    """A paginated slice of results plus the total match count."""

    items: list[T]
    total: int = Field(ge=0)
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


class Message(BaseModel):
    """Simple acknowledgement payload."""

    detail: str
