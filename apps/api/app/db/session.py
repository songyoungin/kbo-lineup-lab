"""Database engine and session factory.

Reads KBO_DATABASE_URL from the environment, defaulting to a local SQLite file.
SQLite connections are configured with check_same_thread=False so FastAPI's
thread-pool workers can share the same connection pool safely.
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL: str = os.environ.get(
    "KBO_DATABASE_URL",
    "sqlite:///./kbo_lineup_lab.db",
)

_is_sqlite = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
