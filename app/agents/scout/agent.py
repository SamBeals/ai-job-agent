"""Scout Agent — discovers and evaluates jobs (placeholder).

Eventually consumes:
  - Candidate profile (target roles, skills, preferences, constraints)
  - Job board / ATS search configuration
  - Scoring / matching rules

Eventually produces:
  - Job records in DISCOVERED → SCORED → RECOMMENDED / AWAITING_APPROVAL
  - fit_score and recommendation_reason for Discord review

Phase 1: no real search or LLM scoring.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScoutResult:
    """Placeholder result from a scout run."""

    jobs_found: int = 0
    jobs_recommended: int = 0
    notes: str = "ScoutAgent is a Phase 1 placeholder — no search performed."


class ScoutAgent:
    """Discovers jobs and recommends strong matches against the career profile."""

    def __init__(self, candidate_profile_path: str | None = None) -> None:
        self.candidate_profile_path = candidate_profile_path

    def run(self) -> ScoutResult:
        """Placeholder scout cycle. Does not search real job boards."""
        return ScoutResult()
