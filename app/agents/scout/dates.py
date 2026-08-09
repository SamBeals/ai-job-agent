"""Date helpers for deriving approximate experience from employment ranges."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas.candidate import WorkExperience


def parse_year_month(value: str | None) -> date | None:
    """Parse YYYY-MM or YYYY into a date (first of month)."""
    if not value:
        return None
    text = value.strip()
    try:
        if len(text) == 4 and text.isdigit():
            return date(int(text), 1, 1)
        parts = text.split("-")
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        return date(year, month, 1)
    except (TypeError, ValueError, IndexError):
        return None


def approximate_years_from_experience(work_experience: list[WorkExperience]) -> float | None:
    """Sum overlapping-aware approximate years from employment date ranges.

    Uses a simple non-overlapping month set so concurrent roles are not double-counted.
    Returns None if no parseable dates exist.
    """
    months: set[tuple[int, int]] = set()
    today = date.today()

    for job in work_experience:
        start = parse_year_month(job.start_date)
        if start is None:
            continue
        end = parse_year_month(job.end_date) if not job.is_current else today
        if end is None:
            end = today
        if end < start:
            continue
        cursor = date(start.year, start.month, 1)
        while cursor <= end:
            months.add((cursor.year, cursor.month))
            if cursor.month == 12:
                cursor = date(cursor.year + 1, 1, 1)
            else:
                cursor = date(cursor.year, cursor.month + 1, 1)

    if not months:
        return None
    return round(len(months) / 12.0, 1)
