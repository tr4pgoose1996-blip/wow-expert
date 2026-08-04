"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.api.dependencies import CurrentUser, SessionDep
from app.schemas.common import Message
from app.schemas.user import (
    LoginRequest,
    PasswordChange,
    RefreshRequest,
    TokenPair,
    UserCreate,
    UserRead,
    UserUpdate,
)
from app.services.auth import AuthService

router = APIRouter()


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
    responses={409: {"description": "Email or username already in use"}},
)
async def register(payload: UserCreate, session: SessionDep) -> UserRead:
    user = await AuthService(session).register(payload)
    return UserRead.model_validate(user)


@router.post(
    "/login",
    response_model=TokenPair,
    summary="Exchange credentials for tokens",
    description="Accepts either the account email or the username.",
    responses={401: {"description": "Incorrect credentials"}},
)
async def login(payload: LoginRequest, session: SessionDep) -> TokenPair:
    _, tokens = await AuthService(session).login(payload.identifier, payload.password)
    return tokens


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Rotate a refresh token",
    description=(
        "Issues a new token pair and revokes the presented refresh token, "
        "so each refresh token is single-use."
    ),
    responses={401: {"description": "Token expired, invalid, or revoked"}},
)
async def refresh(payload: RefreshRequest, session: SessionDep) -> TokenPair:
    return await AuthService(session).refresh(payload.refresh_token)


@router.post(
    "/logout",
    response_model=Message,
    summary="Revoke a refresh token",
)
async def logout(payload: RefreshRequest, session: SessionDep) -> Message:
    await AuthService(session).logout(payload.refresh_token)
    return Message(detail="Logged out.")


@router.get(
    "/me",
    response_model=UserRead,
    summary="Read the authenticated account",
)
async def read_me(current_user: CurrentUser) -> UserRead:
    return UserRead.model_validate(current_user)


@router.patch(
    "/me",
    response_model=UserRead,
    summary="Update the authenticated account",
)
async def update_me(
    payload: UserUpdate, current_user: CurrentUser, session: SessionDep
) -> UserRead:
    user = await AuthService(session).update_profile(current_user, payload)
    return UserRead.model_validate(user)


@router.post(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change the account password",
    responses={401: {"description": "Current password incorrect"}},
)
async def change_password(
    payload: PasswordChange, current_user: CurrentUser, session: SessionDep
) -> Response:
    await AuthService(session).change_password(current_user, payload)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
