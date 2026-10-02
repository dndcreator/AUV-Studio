from __future__ import annotations

from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from .config import settings

Base = declarative_base()


def _ensure_sqlite_path() -> None:
    if settings.database_url.startswith("sqlite:///./"):
        rel = settings.database_url.replace("sqlite:///./", "", 1)
        db_path = Path(".") / rel
        db_path.parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_path()

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
