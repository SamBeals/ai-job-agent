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


def load_discovery_boards(path: str | Path | None) -> dict[str, list[BoardEntry]]:
    """Load registry JSON. Missing/invalid file → empty registry (env fallbacks still apply)."""
    empty: dict[str, list[BoardEntry]] = {
        "greenhouse": [],
        "lever": [],
        "ashby": [],
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
    return {k: [{"company": e.company, "tenant": e.tenant, "metro": e.metro} for e in v] for k, v in data.items()}
