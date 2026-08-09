"""Conservative salary parsing — never confuse hourly with annual."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SalaryParseResult:
    annual_min: int | None
    annual_max: int | None
    currency: str | None
    is_hourly: bool = False
    raw_match: str | None = None
    notes: str | None = None


_CURRENCY = r"(?:USD|US\$|\$)?"
_NUM = r"(?:\$|USD\s*)?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k)?"
_RANGE_SEP = r"(?:\s*[-–—to]+\s*)"


def parse_salary(text: str) -> SalaryParseResult:
    """Extract annual USD base salary when clearly stated.

    Hourly rates are detected and returned with annual_min/max = None so the
    $110k hard filter cannot misread $65/hour as $65,000.
    """
    if not text:
        return SalaryParseResult(None, None, None)

    # Hourly patterns first
    hourly = re.search(
        rf"{_CURRENCY}\s*(\d{{1,3}}(?:,\d{{3}})?|\d+(?:\.\d+)?)\s*(?:/|\s*per\s*)?\s*h(?:ou)?r\b",
        text,
        flags=re.IGNORECASE,
    )
    if hourly and not re.search(r"\b(?:year|annual|annually|/yr|/year)\b", text, re.I):
        # Avoid treating "$120,000/year" as hourly; require hour marker near amount
        return SalaryParseResult(
            annual_min=None,
            annual_max=None,
            currency="USD",
            is_hourly=True,
            raw_match=hourly.group(0),
            notes="Hourly compensation detected; annual base salary left unknown.",
        )

    # Explicit range: $120,000 - $150,000 or $120k-$150k
    range_pat = re.compile(
        rf"{_CURRENCY}\s*{_NUM}{_RANGE_SEP}{_CURRENCY}\s*{_NUM}"
        rf"(?:\s*(?:per\s+year|/year|/yr|annually|a\s+year|USD))?",
        re.IGNORECASE,
    )
    m = range_pat.search(text)
    if m:
        a = _to_int(m.group(1), m.group(2))
        b = _to_int(m.group(3), m.group(4))
        if a is not None and b is not None:
            lo, hi = sorted((a, b))
            # Guard: values that look like hourly in a bare range without year marker
            if hi < 1000 and not re.search(r"year|annual|k\b", m.group(0), re.I):
                return SalaryParseResult(
                    None,
                    None,
                    "USD",
                    is_hourly=True,
                    raw_match=m.group(0),
                    notes="Ambiguous low numeric range; not treated as annual salary.",
                )
            return SalaryParseResult(lo, hi, "USD", raw_match=m.group(0))

    # Single amount with year marker
    single = re.search(
        rf"{_CURRENCY}\s*{_NUM}\s*(?:per\s+year|/year|/yr|annually|a\s+year)\b",
        text,
        re.IGNORECASE,
    )
    if single:
        val = _to_int(single.group(1), single.group(2))
        return SalaryParseResult(val, val, "USD", raw_match=single.group(0))

    # $120k style without explicit year (common in postings)
    k_only = re.search(
        rf"{_CURRENCY}\s*(\d{{2,3}})\s*k{_RANGE_SEP}{_CURRENCY}\s*(\d{{2,3}})\s*k\b",
        text,
        re.IGNORECASE,
    )
    if k_only:
        a, b = int(k_only.group(1)) * 1000, int(k_only.group(2)) * 1000
        lo, hi = sorted((a, b))
        return SalaryParseResult(lo, hi, "USD", raw_match=k_only.group(0))

    return SalaryParseResult(None, None, None)


def _to_int(num: str | None, k_flag: str | None) -> int | None:
    if not num:
        return None
    cleaned = num.replace(",", "")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if k_flag:
        value *= 1000
    return int(round(value))
