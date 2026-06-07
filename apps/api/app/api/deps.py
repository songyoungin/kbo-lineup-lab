"""FastAPI dependency providers for shared resources."""

import os
import secrets
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.db.session import SessionLocal


def get_session() -> Iterator[Session]:
    """Yield a SQLAlchemy session, closing it on exit."""
    with SessionLocal() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def require_api_token(
    x_api_token: Annotated[str | None, Header()] = None,
) -> None:
    """Reject the request unless X-API-Token matches KBO_ADMIN_TOKEN.

    Opt-in guard: when the KBO_ADMIN_TOKEN env var is unset or empty the
    check is a no-op, so local dev and the fixture demo need no setup.

    Raises:
        HTTPException: 401 when the env var is set and the header is
            missing or does not match.
    """
    expected = os.environ.get("KBO_ADMIN_TOKEN")
    if not expected:
        return
    if x_api_token is None or not secrets.compare_digest(x_api_token, expected):
        # WWW-Authenticate is required on 401 responses by RFC 7235.
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
