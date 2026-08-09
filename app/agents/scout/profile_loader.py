"""Load and validate candidate profiles from disk."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from app.schemas.candidate import CandidateProfile


class CandidateProfileError(Exception):
    """Raised when a candidate profile cannot be loaded or validated."""


def load_candidate_profile(path: str | Path) -> CandidateProfile:
    """Load a CandidateProfile from JSON. Fails clearly on missing/invalid files."""
    profile_path = Path(path)
    if not profile_path.exists():
        raise CandidateProfileError(
            f"Candidate profile not found: {profile_path}. "
            "Copy data/candidate_profile.example.json to data/candidate_profile.json "
            "and fill in verified facts."
        )
    try:
        raw = json.loads(profile_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CandidateProfileError(
            f"Candidate profile is not valid JSON: {profile_path}: {exc}"
        ) from exc

    try:
        return CandidateProfile.model_validate(raw)
    except ValidationError as exc:
        raise CandidateProfileError(
            f"Candidate profile failed validation: {profile_path}\n{exc}"
        ) from exc


def profile_from_dict(data: dict) -> CandidateProfile:
    """Validate an in-memory profile dict."""
    try:
        return CandidateProfile.model_validate(data)
    except ValidationError as exc:
        raise CandidateProfileError(f"Invalid candidate profile: {exc}") from exc
