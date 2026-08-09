"""HTML / JSON-LD job content extraction."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html import unescape
from typing import Any

from app.agents.scout.ingestion.arrangement import detect_location_line, detect_remote_status
from app.agents.scout.ingestion.salary import parse_salary


@dataclass
class RawExtractedJob:
    title: str | None = None
    company: str | None = None
    location: str | None = None
    remote_status: str | None = None
    employment_type: str | None = None
    description: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    date_posted: str | None = None
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    responsibilities: list[str] = field(default_factory=list)
    required_years_experience: float | None = None
    education_requirements: list[str] = field(default_factory=list)
    seniority: str | None = None
    method: str = "HTML"
    warnings: list[str] = field(default_factory=list)


def extract_from_html(html: str, *, page_url: str | None = None) -> RawExtractedJob:
    """Layered extraction: JSON-LD JobPosting → metadata → visible text."""
    if not html or not html.strip():
        result = RawExtractedJob(warnings=["Empty HTML document."])
        return result

    json_ld = _extract_json_ld_jobposting(html)
    if json_ld is not None:
        return json_ld

    # Meta tags
    title = _meta(html, "og:title") or _tag_text(html, "title")
    company = _meta(html, "og:site_name")
    description = _meta(html, "og:description") or _meta(html, "description")

    visible = _visible_text(html)
    if not visible or len(visible) < 40:
        return RawExtractedJob(
            title=title,
            company=company,
            description=description,
            method="HTML",
            warnings=["Page had little identifiable job content."],
        )

    salary = parse_salary(visible)
    remote = detect_remote_status(visible)
    location = detect_location_line(visible)

    # Prefer first heading-like line as title if meta missing
    if not title:
        first_lines = [ln.strip() for ln in visible.splitlines() if ln.strip()]
        title = first_lines[0] if first_lines else None

    result = RawExtractedJob(
        title=_clean(title),
        company=_clean(company),
        location=_clean(location),
        remote_status=remote,
        description=visible[:20000],
        salary_min=salary.annual_min,
        salary_max=salary.annual_max,
        salary_currency=salary.currency,
        method="HTML",
        warnings=[],
    )
    if salary.is_hourly:
        result.warnings.append(salary.notes or "Hourly pay detected; annual salary unknown.")
    if not result.title:
        result.warnings.append("Could not confidently identify a job title.")
    return result


def _extract_json_ld_jobposting(html: str) -> RawExtractedJob | None:
    scripts = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for block in scripts:
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        posting = _find_jobposting(data)
        if posting is None:
            continue
        return _from_jobposting(posting)

    return None


def _find_jobposting(data: Any) -> dict[str, Any] | None:
    if isinstance(data, dict):
        typ = data.get("@type")
        types = typ if isinstance(typ, list) else [typ]
        if any(str(t).lower() == "jobposting" for t in types if t):
            return data
        if "@graph" in data:
            found = _find_jobposting(data["@graph"])
            if found:
                return found
    if isinstance(data, list):
        for item in data:
            found = _find_jobposting(item)
            if found:
                return found
    return None


def _from_jobposting(data: dict[str, Any]) -> RawExtractedJob:
    title = data.get("title")
    company = None
    org = data.get("hiringOrganization")
    if isinstance(org, dict):
        company = org.get("name")
    elif isinstance(org, str):
        company = org

    location = None
    jl = data.get("jobLocation")
    if isinstance(jl, dict):
        addr = jl.get("address")
        if isinstance(addr, dict):
            parts = [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")]
            location = ", ".join(p for p in parts if p)
        elif isinstance(addr, str):
            location = addr
    elif isinstance(jl, list) and jl:
        location = _from_jobposting({"jobLocation": jl[0]}).location

    description = data.get("description")
    if isinstance(description, str):
        description = _strip_tags(description)

    employment = data.get("employmentType")
    if isinstance(employment, list):
        employment = ", ".join(str(x) for x in employment)

    salary_min = salary_max = None
    currency = None
    base = data.get("baseSalary")
    if isinstance(base, dict):
        currency = base.get("currency")
        value = base.get("value")
        if isinstance(value, dict):
            salary_min = _num(value.get("minValue"))
            salary_max = _num(value.get("maxValue"))
            if salary_min is None and salary_max is None:
                salary_min = salary_max = _num(value.get("value"))
            unit = str(value.get("unitText") or base.get("unitText") or "").lower()
            if unit in {"hour", "hourly"}:
                return RawExtractedJob(
                    title=_clean(str(title) if title else None),
                    company=_clean(str(company) if company else None),
                    location=_clean(location),
                    employment_type=_clean(str(employment) if employment else None),
                    description=description,
                    date_posted=str(data.get("datePosted")) if data.get("datePosted") else None,
                    method="JSON_LD",
                    warnings=["JSON-LD salary is hourly; annual base salary left unknown."],
                )

    remote = None
    loc_type = data.get("jobLocationType")
    if isinstance(loc_type, str) and "telecommute" in loc_type.lower():
        remote = "remote"

    text_blob = " ".join(x for x in [str(title or ""), str(location or ""), description or ""] if x)
    if remote is None:
        remote = detect_remote_status(text_blob)

    return RawExtractedJob(
        title=_clean(str(title) if title else None),
        company=_clean(str(company) if company else None),
        location=_clean(location),
        remote_status=remote,
        employment_type=_clean(str(employment) if employment else None),
        description=description,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=_clean(currency),
        date_posted=str(data.get("datePosted")) if data.get("datePosted") else None,
        method="JSON_LD",
    )


def _visible_text(html: str) -> str:
    cleaned = re.sub(r"(?is)<(script|style|nav|footer|header|noscript)[^>]*>.*?</\1>", " ", html)
    cleaned = re.sub(r"(?is)<!--.*?-->", " ", cleaned)
    cleaned = _strip_tags(cleaned)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in cleaned.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def _strip_tags(html: str) -> str:
    text = re.sub(r"(?is)<br\s*/?>", "\n", html)
    text = re.sub(r"(?is)</p>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    return unescape(text)


def _meta(html: str, name: str) -> str | None:
    patterns = [
        rf'<meta[^>]+property=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(name)}["\']',
        rf'<meta[^>]+name=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(name)}["\']',
    ]
    for pat in patterns:
        m = re.search(pat, html, flags=re.IGNORECASE)
        if m:
            return unescape(m.group(1)).strip()
    return None


def _tag_text(html: str, tag: str) -> str | None:
    m = re.search(rf"(?is)<{tag}[^>]*>(.*?)</{tag}>", html)
    if not m:
        return None
    return _clean(_strip_tags(m.group(1)))


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None


def _num(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None
