"""Character profile endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Path, Response, status

from app.api.dependencies import CurrentUser, Pagination, SessionDep
from app.schemas.character import CharacterCreate, CharacterRead, CharacterUpdate
from app.schemas.common import Page
from app.services.character import CharacterService

router = APIRouter()


@router.post(
    "",
    response_model=CharacterRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a character to your roster",
    responses={409: {"description": "Duplicate character or roster full"}},
)
async def create_character(
    payload: CharacterCreate, current_user: CurrentUser, session: SessionDep
) -> CharacterRead:
    character = await CharacterService(session).create(current_user.id, payload)
    return CharacterRead.model_validate(character)


@router.get(
    "",
    response_model=Page[CharacterRead],
    summary="List your characters",
    description="Returns your roster ordered by level descending, then name.",
)
async def list_characters(
    current_user: CurrentUser, session: SessionDep, pagination: Pagination
) -> Page[CharacterRead]:
    characters, total = await CharacterService(session).list_for_owner(
        current_user.id, limit=pagination.limit, offset=pagination.offset
    )
    return Page[CharacterRead](
        items=[CharacterRead.model_validate(c) for c in characters],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get(
    "/{character_id}",
    response_model=CharacterRead,
    summary="Read one of your characters",
    responses={404: {"description": "Character not found"}},
)
async def read_character(
    current_user: CurrentUser,
    session: SessionDep,
    character_id: uuid.UUID = Path(description="Character identifier"),
) -> CharacterRead:
    character = await CharacterService(session).get_owned(
        character_id, current_user.id
    )
    return CharacterRead.model_validate(character)


@router.patch(
    "/{character_id}",
    response_model=CharacterRead,
    summary="Update a character",
    responses={
        404: {"description": "Character not found"},
        422: {"description": "Invalid class/role combination"},
    },
)
async def update_character(
    payload: CharacterUpdate,
    current_user: CurrentUser,
    session: SessionDep,
    character_id: uuid.UUID = Path(description="Character identifier"),
) -> CharacterRead:
    character = await CharacterService(session).update(
        character_id, current_user.id, payload
    )
    return CharacterRead.model_validate(character)


@router.delete(
    "/{character_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a character",
    responses={404: {"description": "Character not found"}},
)
async def delete_character(
    current_user: CurrentUser,
    session: SessionDep,
    character_id: uuid.UUID = Path(description="Character identifier"),
) -> Response:
    await CharacterService(session).delete(character_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
