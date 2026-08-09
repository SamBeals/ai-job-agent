"""Conservative skill alias normalization — no aggressive fuzzy matching."""

from __future__ import annotations

# Canonical form -> aliases (lowercase). Matching is bidirectional via reverse map.
SKILL_ALIASES: dict[str, set[str]] = {
    "aws": {"aws", "amazon web services", "amazon aws"},
    "kubernetes": {"kubernetes", "k8s"},
    "postgresql": {"postgresql", "postgres", "psql"},
    "javascript": {"javascript", "js", "ecmascript"},
    "java": {"java"},
    "rest": {
        "rest",
        "rest api",
        "rest apis",
        "restful",
        "restful apis",
        "restful api",
        "rest api design",
    },
    "spring boot": {"spring boot", "springboot"},
    "spring": {"spring"},
    "docker": {"docker"},
    "terraform": {"terraform"},
    "react": {"react", "react.js", "reactjs"},
    "node.js": {"node.js", "nodejs", "node"},
    "mongodb": {"mongodb", "mongo"},
    "python": {"python"},
    "c#": {"c#", "csharp", "c sharp"},
    "sql": {"sql"},
    "selenium": {"selenium"},
    "junit": {"junit"},
    "testng": {"testng"},
    "ci/cd": {"ci/cd", "cicd", "ci cd", "continuous integration"},
    "agile": {"agile"},
    "scrum": {"scrum"},
    "tdd": {"tdd", "test driven development", "test-driven development"},
    "sharepoint": {"sharepoint"},
    "ec2": {"ec2", "amazon ec2"},
    "rds": {"rds", "amazon rds"},
    "s3": {"s3", "amazon s3"},
}


def normalize_skill(raw: str) -> str:
    """Return canonical skill key for matching (lowercase canonical or stripped lower)."""
    key = _normalize_key(raw)
    reverse = _alias_reverse_map()
    return reverse.get(key, key)


def skills_equivalent(a: str, b: str) -> bool:
    """Return True only for exact or explicitly aliased matches.

    Conservatively: Java != JavaScript, React != React Native, AWS != Azure.
    """
    na = normalize_skill(a)
    nb = normalize_skill(b)
    if na == nb:
        return True
    # Spring Boot implies Spring familiarity for matching purposes, but not vice versa
    # as a full equivalence for required-skill satisfaction of "Spring Boot".
    if {na, nb} == {"spring", "spring boot"}:
        return False
    return False


def skills_partially_related(a: str, b: str) -> bool:
    """Weaker relatedness for partial-match reporting only."""
    na = normalize_skill(a)
    nb = normalize_skill(b)
    related_groups = [
        {"spring", "spring boot"},
        {"aws", "ec2", "rds", "s3"},
    ]
    for group in related_groups:
        if na in group and nb in group and na != nb:
            return True
    return False


def _normalize_key(raw: str) -> str:
    return " ".join(raw.strip().lower().replace("_", " ").split())


_REVERSE: dict[str, str] | None = None


def _alias_reverse_map() -> dict[str, str]:
    global _REVERSE
    if _REVERSE is not None:
        return _REVERSE
    reverse: dict[str, str] = {}
    for canonical, aliases in SKILL_ALIASES.items():
        for alias in aliases:
            reverse[_normalize_key(alias)] = canonical
        reverse[_normalize_key(canonical)] = canonical
    _REVERSE = reverse
    return reverse
