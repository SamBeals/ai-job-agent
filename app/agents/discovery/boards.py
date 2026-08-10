"""Load maintainable Discovery employer / ATS board registry."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BoardEntry:
    ats: str
    company: str
    tenant: str
    metro: str | None = None
    enabled: bool = True
    # Workday / Oracle fields (optional for other ATS)
    host: str | None = None
    site: str | None = None
    career_base_url: str | None = None
    location_facet_ids: tuple[str, ...] = ()


def load_discovery_boards(path: str | Path | None) -> dict[str, list[BoardEntry]]:
    """Load registry JSON. Missing/invalid file → empty registry (env fallbacks still apply)."""
    empty: dict[str, list[BoardEntry]] = {
        "greenhouse": [],
        "lever": [],
        "ashby": [],
        "workday": [],
        "oracle": [],
    }
    if not path:
        return empty
    p = Path(path)
    if not p.is_file():
        logger.info("discovery_boards_missing path=%s", p)
        return empty
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("discovery_boards_invalid path=%s error=%s", p, type(exc).__name__)
        return empty

    out = {k: [] for k in empty}
    for ats, key in (("greenhouse", "board"), ("lever", "site"), ("ashby", "board")):
        for row in data.get(ats) or []:
            if not isinstance(row, dict):
                continue
            if row.get("enabled") is False:
                continue
            tenant = (row.get(key) or row.get("tenant") or "").strip()
            company = (row.get("company") or tenant).strip()
            if not tenant:
                continue
            out[ats].append(
                BoardEntry(
                    ats=ats,
                    company=company or tenant,
                    tenant=tenant,
                    metro=(row.get("metro") or None),
                    enabled=True,
                )
            )

    for row in data.get("workday") or []:
        if not isinstance(row, dict):
            continue
        if row.get("enabled") is False:
            continue
        host = (row.get("host") or "").strip().lower()
        tenant = (row.get("tenant") or "").strip()
        site = (row.get("site") or "").strip()
        company = (row.get("company") or tenant or host).strip()
        if not host or not tenant or not site:
            logger.warning(
                "discovery_boards_workday_incomplete company=%s", company or "?"
            )
            continue
        # Drop scheme if pasted
        host = host.replace("https://", "").replace("http://", "").split("/")[0]
        out["workday"].append(
            BoardEntry(
                ats="workday",
                company=company or tenant,
                tenant=tenant,
                metro=(row.get("metro") or None),
                enabled=True,
                host=host,
                site=site,
            )
        )

    for row in data.get("oracle") or []:
        if not isinstance(row, dict):
            continue
        if row.get("enabled") is False:
            continue
        host = (row.get("host") or "").strip().lower()
        site_number = (row.get("site_number") or row.get("tenant") or "").strip()
        site_path = (row.get("site_path") or row.get("site") or site_number).strip()
        career_base = (row.get("career_base_url") or "").strip().rstrip("/")
        company = (row.get("company") or site_number or host).strip()
        if not host or not site_number or not site_path or not career_base:
            logger.warning(
                "discovery_boards_oracle_incomplete company=%s", company or "?"
            )
            continue
        host = host.replace("https://", "").replace("http://", "").split("/")[0]
        if career_base.startswith("http://"):
            career_base = "https://" + career_base[len("http://") :]
        elif not career_base.startswith("https://"):
            career_base = "https://" + career_base.lstrip("/")
        facet_raw = row.get("location_facet_ids") or []
        facets: list[str] = []
        if isinstance(facet_raw, list):
            for item in facet_raw:
                s = str(item).strip()
                if s:
                    facets.append(s)
        out["oracle"].append(
            BoardEntry(
                ats="oracle",
                company=company or site_number,
                tenant=site_number,
                metro=(row.get("metro") or None),
                enabled=True,
                host=host,
                site=site_path,
                career_base_url=career_base,
                location_facet_ids=tuple(facets),
            )
        )
    return out


def merge_tenants(
    registry: list[BoardEntry],
    env_tokens: list[str],
    env_names: dict[str, str] | None = None,
) -> list[BoardEntry]:
    """Registry first, then env tokens not already present."""
    env_names = env_names or {}
    seen = {e.tenant.lower() for e in registry}
    out = list(registry)
    for token in env_tokens:
        t = token.strip()
        if not t or t.lower() in seen:
            continue
        out.append(
            BoardEntry(
                ats=registry[0].ats if registry else "unknown",
                company=env_names.get(t) or t.replace("-", " ").title(),
                tenant=t,
            )
        )
        seen.add(t.lower())
    return out


def company_map(entries: list[BoardEntry]) -> dict[str, str]:
    return {e.tenant: e.company for e in entries}


def tenant_list(entries: list[BoardEntry]) -> list[str]:
    return [e.tenant for e in entries]


def as_debug_dict(data: dict[str, list[BoardEntry]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, entries in data.items():
        rows = []
        for e in entries:
            row = {"company": e.company, "tenant": e.tenant, "metro": e.metro}
            if e.host:
                row["host"] = e.host
            if e.site:
                row["site"] = e.site
            if e.career_base_url:
                row["career_base_url"] = e.career_base_url
            if e.location_facet_ids:
                row["location_facet_ids"] = list(e.location_facet_ids)
            rows.append(row)
        out[k] = rows
    return out
