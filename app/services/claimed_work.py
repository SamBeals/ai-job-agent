"""Claimed work identity — primitives only, safe across Session boundaries."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ClaimedWork:
    """Stable identifiers captured while an AgentWorkItem is still session-bound.

    Never pass live SQLAlchemy ORM instances across Session commit/close boundaries.
    """

    work_item_id: int
    agent_type: str
    task_type: str
    job_id: int | None = None
    pipeline_id: int | None = None
    discovery_run_id: int | None = None
