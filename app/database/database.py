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
    from app.models import (  # noqa: F401
        approval,
        application,
        discovery,
        job,
        pipeline,
        resume_plan,
        scout_evaluation,
        submission_authorization,
        work_item,
    )

    target = engine_override or engine
    Base.metadata.create_all(bind=target)
    _migrate_sqlite_agent_work_items(target)


def _migrate_sqlite_agent_work_items(target: Engine) -> None:
    """Widen agent_work_items for Discovery (nullable job/pipeline + discovery_run_id).

    SQLite cannot DROP NOT NULL in place — rebuild the table when needed.
    """
    if not str(target.url).startswith("sqlite"):
        return
    with target.begin() as conn:
        rows = conn.exec_driver_sql("PRAGMA table_info(agent_work_items)").fetchall()
        if not rows:
            return
        cols = {r[1]: r for r in rows}  # name -> pragma row (cid, name, type, notnull, ...)
        needs_rebuild = False
        if "discovery_run_id" not in cols:
            needs_rebuild = True
        for required_nullable in ("job_id", "pipeline_id"):
            if required_nullable in cols and cols[required_nullable][3] == 1:
                needs_rebuild = True
        if not needs_rebuild:
            return

        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.exec_driver_sql(
            """
            CREATE TABLE agent_work_items_new (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER,
                pipeline_id INTEGER,
                discovery_run_id INTEGER,
                agent_type VARCHAR(50) NOT NULL,
                task_type VARCHAR(80) NOT NULL,
                status VARCHAR(50) NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                input_metadata JSON,
                output_metadata JSON,
                created_at DATETIME NOT NULL,
                started_at DATETIME,
                completed_at DATETIME,
                failed_at DATETIME,
                heartbeat_at DATETIME,
                claimed_by VARCHAR(120),
                FOREIGN KEY(job_id) REFERENCES jobs (id),
                FOREIGN KEY(pipeline_id) REFERENCES application_pipelines (id),
                FOREIGN KEY(discovery_run_id) REFERENCES discovery_runs (id),
                CONSTRAINT uq_work_item_pipeline_agent_task
                    UNIQUE (pipeline_id, agent_type, task_type)
            )
            """
        )
        # discovery_runs must exist before FK — create_all already ran
        old_cols = [r[1] for r in rows]
        copy_cols = [
            c
            for c in [
                "id",
                "job_id",
                "pipeline_id",
                "discovery_run_id",
                "agent_type",
                "task_type",
                "status",
                "attempt_count",
                "error_message",
                "input_metadata",
                "output_metadata",
                "created_at",
                "started_at",
                "completed_at",
                "failed_at",
                "heartbeat_at",
                "claimed_by",
            ]
            if c in old_cols or c == "discovery_run_id"
        ]
        # Only copy columns that exist in old table (discovery_run_id may be new → NULL)
        src_cols = [c for c in copy_cols if c in old_cols]
        dst_cols = list(src_cols)
        if "discovery_run_id" not in old_cols and "discovery_run_id" in copy_cols:
            # omit — defaults NULL
            pass
        col_csv = ", ".join(src_cols)
        conn.exec_driver_sql(
            f"INSERT INTO agent_work_items_new ({col_csv}) "
            f"SELECT {col_csv} FROM agent_work_items"
        )
        conn.exec_driver_sql("DROP TABLE agent_work_items")
        conn.exec_driver_sql(
            "ALTER TABLE agent_work_items_new RENAME TO agent_work_items"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_agent_work_items_job_id "
            "ON agent_work_items (job_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_agent_work_items_pipeline_id "
            "ON agent_work_items (pipeline_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_agent_work_items_discovery_run_id "
            "ON agent_work_items (discovery_run_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_agent_work_items_agent_type "
            "ON agent_work_items (agent_type)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_agent_work_items_status "
            "ON agent_work_items (status)"
        )
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")



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
