"""FastAPI dependency providers for shared resources."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import SessionLocal


def get_session() -> Iterator[Session]:
    """Yield a SQLAlchemy session, closing it on exit."""
    with SessionLocal() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
