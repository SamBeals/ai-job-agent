"""Desirability scoring — how well a job matches *known* candidate preferences.

Qualification is separate. Unknown preferences do not penalize.
Hard filters remain in hard_filters.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.job_posting import NormalizedJob
from app.schemas.preferences import JobPreferences


@dataclass(frozen=True)
class DesirabilityBreakdown:
    """Explainable desirability components."""

    score: int
    signals: dict[str, float]
    strengths: list[str]
    concerns: list[str]
    unknowns: list[str]


def score_desirability(prefs: JobPreferences, job: NormalizedJob) -> DesirabilityBreakdown:
    """Compute 0–100 desirability with human-readable strengths/concerns."""
    signals: dict[str, float] = {}
    weights: dict[str, float] = {}
    strengths: list[str] = []
    concerns: list[str] = []
    unknowns: list[str] = []

    role = _role_signal(prefs, job, strengths, concerns, unknowns)
    if role is not None:
        signals["role"] = role
        weights["role"] = 1.2

    arrangement = _arrangement_signal(prefs, job, strengths, concerns, unknowns)
    if arrangement is not None:
        signals["arrangement"] = arrangement
        weights["arrangement"] = 1.0

    location = _location_signal(prefs, job, strengths, concerns, unknowns)
    if location is not None:
        signals["location"] = location
        weights["location"] = 1.1

    development = _development_focus_signal(prefs, job, strengths, concerns, unknowns)
    if development is not None:
        signals["development_focus"] = development
        weights["development_focus"] = 1.3

    backend = _backend_focus_signal(prefs, job, strengths, concerns, unknowns)
    if backend is not None:
        signals["backend_focus"] = backend
        weights["backend_focus"] = 1.3

    salary = _salary_signal(prefs, job, strengths, concerns, unknowns)
    if salary is not None:
        signals["salary"] = salary
        weights["salary"] = 0.8

    seniority = _seniority_signal(prefs, job, strengths, concerns, unknowns)
    if seniority is not None:
        signals["seniority"] = seniority
        weights["seniority"] = 0.5

    if not signals:
        return DesirabilityBreakdown(
            score=70,
            signals={},
            strengths=["No known preferences actively scored; desirability left neutral."],
            concerns=[],
            unknowns=unknowns,
        )

    total_w = sum(weights[k] for k in signals)
    raw = sum(signals[k] * weights[k] for k in signals) / total_w
    return DesirabilityBreakdown(
        score=_clamp(raw),
        signals=signals,
        strengths=strengths or ["Preference signals evaluated."],
        concerns=concerns,
        unknowns=unknowns,
    )


def _clamp(value: float) -> int:
    return max(0, min(100, int(round(value))))


def _job_text(job: NormalizedJob) -> str:
    parts = [
        job.title or "",
        job.description or "",
        " ".join(job.responsibilities or []),
        " ".join(job.required_skills or []),
        " ".join(job.preferred_skills or []),
    ]
    return " ".join(parts).lower()


def _normalize_arrangement(value: str | None) -> str | None:
    if not value:
        return None
    v = value.lower().strip().replace("_", " ").replace("-", " ")
    v = " ".join(v.split())
    if v in {"remote", "fully remote", "work from home", "wfh"}:
        return "remote"
    if v in {"hybrid"}:
        return "hybrid"
    if v in {"onsite", "on site", "office", "in office", "in person"}:
        return "onsite"
    return v


# --- Role matching -----------------------------------------------------------

_ROLE_STOPWORDS = {
    "the",
    "and",
    "or",
    "for",
    "with",
    "a",
    "an",
    "of",
    "in",
    "to",
    "sr",
    "senior",
    "junior",
    "mid",
    "staff",
    "lead",
    "principal",
    "i",
    "ii",
    "iii",
}


_TOKEN_SYNONYMS: dict[str, set[str]] = {
    "engineer": {"engineer", "developer", "sde"},
    "developer": {"developer", "engineer", "sde"},
    "software": {"software", "application"},
    "application": {"application", "software"},
    "backend": {"backend", "back-end", "server-side"},
    "java": {"java"},
}


def _role_tokens(text: str) -> set[str]:
    cleaned = (
        text.lower()
        .replace("/", " ")
        .replace("-", " ")
        .replace("(", " ")
        .replace(")", " ")
    )
    tokens = {t for t in cleaned.split() if t and t not in _ROLE_STOPWORDS and len(t) > 1}
    expanded: set[str] = set()
    for t in tokens:
        expanded.add(t)
        for canonical, aliases in _TOKEN_SYNONYMS.items():
            if t == canonical or t in aliases:
                expanded |= aliases
                expanded.add(canonical)
    return expanded


def _role_signal(
    prefs: JobPreferences,
    job: NormalizedJob,
    strengths: list[str],
    concerns: list[str],
    unknowns: list[str],
) -> float | None:
    targets = list(prefs.target_roles or []) + list(prefs.acceptable_roles or [])
    if not targets and not prefs.excluded_roles:
        unknowns.append("Role preference unknown — not used in desirability.")
        return None

    title = job.title or ""
    title_l = title.lower()
    if prefs.excluded_roles:
        for role in prefs.excluded_roles:
            if role.lower() in title_l:
                concerns.append(f"Title matches excluded role pattern: {role}.")
                return 8.0

    if not targets:
        return None

    title_tokens = _role_tokens(title)
    frontend_title = any(
        x in title_l
        for x in ("frontend", "front-end", "front end", "ui engineer", "ui developer", "ux engineer")
    )
    backend_title = any(x in title_l for x in ("backend", "back-end", "back end", "java "))
    best = 35.0
    best_label = None
    for role in targets:
        role_tokens = _role_tokens(role)
        if not role_tokens:
            continue
        overlap = title_tokens & role_tokens
        ratio = len(overlap) / max(len(role_tokens), 1)
        score = 35 + ratio * 65
        # Backend/java family bonus
        if {"backend", "java"} & role_tokens and {"backend", "java"} & title_tokens:
            score = max(score, 92)
        # General software-engineer family — do not award to clear frontend-only titles
        if (
            not frontend_title
            and {"software", "application"} & role_tokens
            and ({"engineer", "developer", "sde"} & title_tokens)
        ):
            score = max(score, 88)
        if (
            not frontend_title
            and (
                "software development engineer" in title_l
                or "software developer" in title_l
                or "software engineer" in title_l
            )
        ):
            score = max(score, 90)
        # Frontend-only titles should not look like strong target-role matches
        if frontend_title and not backend_title:
            score = min(score, 48)
        if score > best:
            best = score
            best_label = role

    if best >= 80:
        strengths.append(
            f"Role aligns with target family"
            + (f" ({best_label})" if best_label else "")
            + "."
        )
    elif best < 55:
        concerns.append("Role alignment with target software-engineering preferences is weak.")
    else:
        strengths.append("Role has moderate alignment with target role preferences.")
    return best


# --- Work arrangement --------------------------------------------------------

def _arrangement_signal(
    prefs: JobPreferences,
    job: NormalizedJob,
    strengths: list[str],
    concerns: list[str],
    unknowns: list[str],
) -> float | None:
    order = prefs.work_arrangement_order
    has_arrangement_pref = bool(
        order
        or prefs.remote_preference
        or prefs.remote_required is not None
        or prefs.hybrid_allowed is not None
        or prefs.onsite_allowed is not None
    )
    if not has_arrangement_pref:
        return None

    status = _normalize_arrangement(job.remote_status)
    if status is None:
        unknowns.append("Work arrangement compatibility unknown.")
        return None

    # Explicit disallow still handled by hard filters; here we rank.
    if prefs.remote_required is True:
        if status == "remote":
            strengths.append("Remote arrangement matches remote-required preference.")
            return 100.0
        if status == "hybrid":
            concerns.append("Hybrid arrangement while remote is required.")
            return 40.0
        concerns.append("On-site arrangement conflicts with remote-required preference.")
        return 10.0

    if order:
        normalized_order = [
            x for x in (_normalize_arrangement(item) for item in order) if x
        ]
        if status in normalized_order:
            rank = normalized_order.index(status)
            # Rank 0 → 96, rank 1 → 88, rank 2 → 80 (remote still high)
            score = 96 - (rank * 8)
            if status == "hybrid":
                strengths.append("Hybrid work arrangement matches candidate preference.")
            elif status == "onsite":
                strengths.append("On-site work arrangement matches candidate preference.")
            elif status == "remote":
                strengths.append(
                    "Fully remote is acceptable; hybrid/on-site is preferred but not required."
                )
                if rank > 0:
                    concerns.append(
                        "Position is fully remote; acceptable, but hybrid/on-site is preferred."
                    )
            return float(score)

    if prefs.remote_preference:
        pref = _normalize_arrangement(prefs.remote_preference)
        if pref and pref == status:
            strengths.append(f"{status.title()} arrangement matches preferred arrangement.")
            return 92.0
        if status == "remote":
            strengths.append("Remote is allowed; not a dealbreaker.")
            return 78.0
        return 70.0

    # Allowed flags only
    if status == "remote" and prefs.remote_required is False:
        strengths.append("Remote is allowed.")
        return 80.0
    if status == "hybrid" and prefs.hybrid_allowed is not False:
        strengths.append("Hybrid arrangement is allowed.")
        return 90.0
    if status == "onsite" and prefs.onsite_allowed is not False:
        strengths.append("On-site arrangement is allowed.")
        return 88.0
    return 70.0


# --- Geography ---------------------------------------------------------------

_PHOENIX_METRO_HINTS = {
    "phoenix",
    "chandler",
    "tempe",
    "mesa",
    "scottsdale",
    "gilbert",
    "cave creek",
    "glendale",
    "peoria",
    "surprise",
    "avondale",
    "goodyear",
    "queen creek",
    "fountain hills",
    "paradise valley",
    "ahwatukee",
    "east valley",
    "arizona",
    "az",
}


def _location_signal(
    prefs: JobPreferences,
    job: NormalizedJob,
    strengths: list[str],
    concerns: list[str],
    unknowns: list[str],
) -> float | None:
    has_geo_pref = bool(
        prefs.preferred_locations
        or prefs.acceptable_locations
        or prefs.home_location
        or prefs.relocation_allowed is not None
    )
    if not has_geo_pref:
        return None

    status = _normalize_arrangement(job.remote_status)
    if status == "remote":
        strengths.append("Remote role — geography is flexible / acceptable.")
        return 82.0

    if not job.location:
        unknowns.append("Location compatibility unknown.")
        return None

    loc = job.location.lower()

    if prefs.preferred_locations:
        for preferred in prefs.preferred_locations:
            p = preferred.lower()
            if p in loc or any(tok in loc for tok in p.replace(",", " ").split() if len(tok) > 2):
                # Stronger when "chandler" specifically matches
                if "chandler" in p and "chandler" in loc:
                    strengths.append("Chandler location strongly matches geographic preference.")
                    return 100.0
                strengths.append(f"Location strongly matches preferred geography ({preferred}).")
                return 96.0

    if prefs.acceptable_locations:
        for acceptable in prefs.acceptable_locations:
            a = acceptable.lower()
            city = a.split(",")[0].strip()
            if city and city in loc:
                strengths.append(
                    f"Location in preferred metro area ({job.location}) — desirable commute geography."
                )
                return 90.0

    # Soft Phoenix-metro recognition when home/preferred imply AZ
    home = (prefs.home_location or "").lower()
    prefers_az = "arizona" in home or "az" in home or any(
        "arizona" in (x or "").lower() or "az" in (x or "").lower()
        for x in (prefs.preferred_locations or []) + (prefs.acceptable_locations or [])
    )
    if prefers_az and any(h in loc for h in _PHOENIX_METRO_HINTS):
        strengths.append("Phoenix Metro / East Valley location is desirable.")
        return 88.0

    if job.requires_relocation is True:
        concerns.append("Role appears to require relocation away from preferred geography.")
        return 28.0

    if prefers_az and not any(h in loc for h in _PHOENIX_METRO_HINTS):
        concerns.append(
            f"Location ({job.location}) is outside the preferred Phoenix Metro area."
        )
        return 32.0

    concerns.append(f"Location ({job.location}) is outside preferred geography.")
    return 40.0


# --- Development / backend focus --------------------------------------------

_DEV_POSITIVE = {
    "software engineer",
    "software developer",
    "software development",
    "application developer",
    "backend",
    "build",
    "develop",
    "design and implement",
    "implement",
    "api",
    "services",
    "microservices",
    "feature",
    "codebase",
}

_DEV_NEGATIVE_PRIMARY = {
    "help desk",
    "service desk",
    "desktop support",
    "production support only",
    "operations support",
    "noc ",
    "project manager",
    "program manager",
    "people manager",
    "engineering manager",
    "manual qa",
    "manual tester",
    "system administrator",
    "sysadmin",
    "scrum master only",
}


def _development_focus_signal(
    prefs: JobPreferences,
    job: NormalizedJob,
    strengths: list[str],
    concerns: list[str],
    unknowns: list[str],
) -> float | None:
    if prefs.prefers_software_development is not True:
        return None

    text = _job_text(job)
    title = (job.title or "").lower()

    negative_hits = sum(1 for n in _DEV_NEGATIVE_PRIMARY if n in text or n in title)
    positive_hits = sum(1 for p in _DEV_POSITIVE if p in text)

    # Title-based classification
    if any(
        x in title
        for x in (
            "project manager",
            "program manager",
            "scrum master",
            "help desk",
            "desktop support",
            "system administrator",
            "sysadmin",
        )
    ):
        concerns.append("Role appears centered on support/management rather than software development.")
        return 22.0

    if any(
        x in title
        for x in (
            "software engineer",
            "software developer",
            "java developer",
            "application developer",
            "backend",
            "full stack",
            "fullstack",
        )
    ):
        strengths.append("Role is primarily software development.")
        # Still check for support-primary language
        if negative_hits >= 2 and positive_hits <= 1:
            concerns.append("Posting emphasizes support/operations more than building software.")
            return 45.0
        return 94.0

    if "qa" in title and "automation" not in title and "sdet" not in title:
        concerns.append("Role appears QA-centered rather than software development.")
        return 35.0

    if "support" in title and "engineer" in title and "software" not in title:
        concerns.append("Role appears support-centered; development may be incidental.")
        return 40.0

    if positive_hits >= 3:
        strengths.append("Responsibilities indicate substantial software development work.")
        return 88.0

    if negative_hits >= 2 and positive_hits == 0:
        concerns.append("Role appears primarily support/operations rather than software development.")
        return 30.0

    unknowns.append("Software-development focus of the role is only partially clear from the posting.")
    return 60.0


_BACKEND_HINTS = {
    "backend",
    "back-end",
    "back end",
    "server-side",
    "api",
    "apis",
    "spring",
    "spring boot",
    "java",
    "microservices",
    "distributed",
    "database",
    "sql",
    "kubernetes",
    "service development",
}

_FRONTEND_HINTS = {
    "frontend",
    "front-end",
    "front end",
    "react",
    "angular",
    "vue",
    "css",
    "html",
    "ui engineer",
    "ui developer",
    "ux engineer",
}


def _backend_focus_signal(
    prefs: JobPreferences,
    job: NormalizedJob,
    strengths: list[str],
    concerns: list[str],
    unknowns: list[str],
) -> float | None:
    if prefs.prefers_backend is not True:
        return None

    text = _job_text(job)
    title = (job.title or "").lower()
    backend_hits = sum(1 for h in _BACKEND_HINTS if h in text)
    frontend_hits = sum(1 for h in _FRONTEND_HINTS if h in text)

    title_backend = any(h in title for h in ("backend", "back-end", "java", "api"))
    title_frontend = any(
        h in title for h in ("frontend", "front-end", "react", "ui ", "ux ")
    )
    title_fullstack = "full stack" in title or "fullstack" in title or "full-stack" in title

    if title_frontend and not title_backend and not title_fullstack:
        concerns.append("Position appears frontend-heavy / frontend-focused.")
        return 28.0

    if title_backend and not title_frontend:
        strengths.append("Role is primarily backend software development.")
        return 96.0

    if title_fullstack or ("full stack" in text or "fullstack" in text):
        if backend_hits >= frontend_hits:
            strengths.append(
                "Full-stack role with substantial backend responsibilities — still attractive."
            )
            return 84.0
        concerns.append("Full-stack role appears frontend-leaning.")
        return 58.0

    if backend_hits >= 4 and frontend_hits <= 1:
        strengths.append("Responsibilities indicate backend-heavy development.")
        return 93.0

    if frontend_hits >= 4 and backend_hits <= 1:
        concerns.append("Position appears frontend-heavy.")
        return 35.0

    if backend_hits > frontend_hits:
        strengths.append("Work mix appears backend-oriented.")
        return 80.0

    if frontend_hits > backend_hits:
        concerns.append("Work mix appears more frontend-oriented than preferred.")
        return 45.0

    unknowns.append("Backend vs frontend emphasis is unclear from the posting.")
    return 65.0


def _salary_signal(
    prefs: JobPreferences,
    job: NormalizedJob,
    strengths: list[str],
    concerns: list[str],
    unknowns: list[str],
) -> float | None:
    if prefs.minimum_base_salary is None:
        return None
    if job.salary_min is None and job.salary_max is None:
        unknowns.append("Compensation was not disclosed.")
        return None
    offer_max = job.salary_max if job.salary_max is not None else job.salary_min
    assert offer_max is not None
    if offer_max >= prefs.minimum_base_salary:
        strengths.append(
            f"Listed compensation can meet the ${prefs.minimum_base_salary:,} minimum."
        )
        return 88.0
    # Hard filter should usually catch this; keep low if reached
    concerns.append("Listed compensation appears below the candidate minimum.")
    return 15.0


def _seniority_signal(
    prefs: JobPreferences,
    job: NormalizedJob,
    strengths: list[str],
    concerns: list[str],
    unknowns: list[str],
) -> float | None:
    # Unknown seniority preference must not penalize.
    if not (
        prefs.preferred_seniority
        or prefs.acceptable_seniority
        or prefs.excluded_seniority
    ):
        return None
    if not job.seniority:
        unknowns.append("Seniority compatibility unknown.")
        return None
    s = job.seniority.lower()
    if prefs.excluded_seniority and any(x.lower() in s for x in prefs.excluded_seniority):
        concerns.append(f"Seniority '{job.seniority}' is excluded by preference.")
        return 10.0
    preferred = list(prefs.preferred_seniority or []) + list(prefs.acceptable_seniority or [])
    if preferred and any(x.lower() in s or s in x.lower() for x in preferred):
        strengths.append(f"Seniority '{job.seniority}' matches preference.")
        return 90.0
    if preferred:
        concerns.append(f"Seniority '{job.seniority}' is outside preferred seniority.")
        return 45.0
    return None
