"""SQLAlchemy database setup — SQLite now, PostgreSQL-ready later."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    """Create parent directory for SQLite file URLs if needed."""
    if database_url.startswith("sqlite:///"):
        raw_path = database_url.removeprefix("sqlite:///")
        if raw_path not in {":memory:", ""} and not raw_path.startswith("/"):
            Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
        elif raw_path.startswith("/") or raw_path.startswith("./"):
            Path(raw_path).parent.mkdir(parents=True, exist_ok=True)


def create_db_engine(database_url: str | None = None) -> Engine:
    """Create a SQLAlchemy engine configured for SQLite or PostgreSQL."""
    url = database_url or get_settings().database_url
    _ensure_sqlite_parent_dir(url)

    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    engine = create_engine(url, connect_args=connect_args, future=True)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:  # noqa: ARG001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = create_db_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db(engine_override: Engine | None = None) -> None:
    """Create all tables. Import models so metadata is populated."""
    # Import models for side-effect registration on Base.metadata
    from app.models import approval, application, job  # noqa: F401

    target = engine_override or engine
    Base.metadata.create_all(bind=target)


def get_session() -> Generator[Session, None, None]:
    """Yield a database session (for FastAPI dependency injection)."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session_factory(engine_override: Engine | None = None) -> sessionmaker[Session]:
    """Create a session factory bound to an optional engine (useful in tests)."""
    target = engine_override or engine
    return sessionmaker(bind=target, autoflush=False, autocommit=False, future=True)
