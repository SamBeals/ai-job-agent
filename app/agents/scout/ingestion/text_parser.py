"""Raw pasted job-text extraction → structured fields (no hallucination)."""

from __future__ import annotations

import re

from app.agents.scout.ingestion.arrangement import detect_location_line, detect_remote_status
from app.agents.scout.ingestion.html_extract import RawExtractedJob
from app.agents.scout.ingestion.salary import parse_salary


def extract_from_text(
    text: str,
    *,
    title: str | None = None,
    company: str | None = None,
    source_url: str | None = None,
) -> RawExtractedJob:
    raw = (text or "").strip()
    warnings: list[str] = []
    if not raw:
        return RawExtractedJob(warnings=["Empty job text."], method="TEXT")

    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    inferred_title = title or (lines[0] if lines else None)
    inferred_company = company

    if inferred_company is None:
        for ln in lines[1:4]:
            if re.search(r"(?i)^(company|employer)\s*[:\-]", ln):
                inferred_company = re.split(r"[:\-]", ln, maxsplit=1)[-1].strip()
                break
            # Second line often company if short and not a location/salary line
            if (
                inferred_company is None
                and len(ln) < 80
                and not re.search(r"(?i)salary|\$|remote|hybrid|on-?site|location", ln)
                and not re.search(r",\s*[A-Z]{2}\b", ln)
            ):
                inferred_company = ln
                break

    salary = parse_salary(raw)
    remote = detect_remote_status(raw)
    location = detect_location_line(raw)
    required, preferred = _extract_skill_sections(raw)
    responsibilities = _extract_bullet_section(raw, ("responsibilities", "what you'll do", "you will"))
    years = _extract_years(raw)
    education = _extract_education(raw)
    seniority = _extract_seniority(inferred_title or "", raw)
    employment = _extract_employment_type(raw)

    if salary.is_hourly:
        warnings.append(salary.notes or "Hourly compensation detected; annual salary unknown.")

    return RawExtractedJob(
        title=_clean(inferred_title),
        company=_clean(inferred_company) or "Unknown Company",
        location=_clean(location),
        remote_status=remote,
        employment_type=employment,
        description=raw,
        salary_min=salary.annual_min,
        salary_max=salary.annual_max,
        salary_currency=salary.currency,
        required_skills=required,
        preferred_skills=preferred,
        responsibilities=responsibilities,
        required_years_experience=years,
        education_requirements=education,
        seniority=seniority,
        method="TEXT",
        warnings=warnings,
    )


def _extract_skill_sections(text: str) -> tuple[list[str], list[str]]:
    required: list[str] = []
    preferred: list[str] = []

    req_block = _section_block(
        text,
        (
            "required qualifications",
            "requirements",
            "required skills",
            "minimum qualifications",
            "you must have",
        ),
    )
    pref_block = _section_block(
        text,
        (
            "preferred qualifications",
            "nice to have",
            "preferred skills",
            "bonus",
            "desired skills",
        ),
    )
    required.extend(_bullets(req_block))
    preferred.extend(_bullets(pref_block))
    return _dedupe(required), _dedupe(preferred)


def _section_block(text: str, headers: tuple[str, ...]) -> str:
    lower = text.lower()
    for header in headers:
        idx = lower.find(header)
        if idx < 0:
            continue
        start = idx + len(header)
        # end at next header-like line
        rest = text[start:]
        m = re.search(
            r"(?im)^\s*(?:responsibilities|requirements|preferred|qualifications|benefits|about\s+the)\b",
            rest,
        )
        return rest[: m.start()] if m else rest[:2000]
    return ""


def _bullets(block: str) -> list[str]:
    items: list[str] = []
    for ln in block.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if re.match(r"^[-*•]\s+", ln) or re.match(r"^\d+[.)]\s+", ln):
            items.append(re.sub(r"^[-*•\d.)\s]+", "", ln).strip())
    return [i for i in items if 1 < len(i) < 120]


def _extract_bullet_section(text: str, headers: tuple[str, ...]) -> list[str]:
    return _bullets(_section_block(text, headers))


def _extract_years(text: str) -> float | None:
    m = re.search(r"(\d+)\+?\s*\+?\s*years?(?:\s+of)?\s+experience", text, re.I)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _extract_education(text: str) -> list[str]:
    out: list[str] = []
    if re.search(r"bachelor'?s|b\.s\.|bs\b", text, re.I):
        out.append("Bachelor's degree")
    if re.search(r"master'?s|m\.s\.|ms\b", text, re.I):
        out.append("Master's degree")
    if re.search(r"\bph\.?d\b", text, re.I):
        out.append("PhD")
    return out


def _extract_seniority(title: str, text: str) -> str | None:
    blob = f"{title} {text[:300]}".lower()
    for level in ("principal", "staff", "lead", "senior", "junior", "intern"):
        if re.search(rf"\b{level}\b", blob):
            return level
    return None


def _extract_employment_type(text: str) -> str | None:
    if re.search(r"\bfull[\s\-]?time\b", text, re.I):
        return "full_time"
    if re.search(r"\bcontract[\s\-]?to[\s\-]?hire\b", text, re.I):
        return "contract_to_hire"
    if re.search(r"\bcontract\b", text, re.I):
        return "contract"
    if re.search(r"\bpart[\s\-]?time\b", text, re.I):
        return "part_time"
    return None


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None
