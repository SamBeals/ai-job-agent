"""Scout qualification prompt templates (versioned)."""

from __future__ import annotations

import json
from typing import Any

PROMPT_VERSION = "qualification-v1"

SYSTEM_PROMPT = """You are Scout's qualification analyst for an AI job agent.

Your ONLY job is to evaluate how well VERIFIED candidate evidence matches a job posting.

Rules:
1. Use ONLY the supplied candidate evidence. Never invent employers, titles, technologies, years, metrics, degrees, certifications, proficiency, or production experience.
2. Absence of evidence means NO_EVIDENCE or UNKNOWN — not proof the candidate cannot do the work.
3. Distinguish REQUIRED vs PREFERRED vs INFERRED_CONTEXT. Do not promote stack mentions into REQUIRED without textual support.
4. Distinguish PROFESSIONAL_EXPERIENCE from LISTED_SKILL. Listed skills have unknown depth/years.
5. Never infer technology-specific years from total professional software-engineering years.
6. Transferable experience is allowed (e.g. REST APIs ↔ RESTful APIs) but preserve technology gaps (AWS ≠ Azure, Java ≠ JavaScript).
7. Do NOT evaluate whether the candidate wants the job (desirability is handled elsewhere).
8. Do NOT authorize applications. Never suggest APPROVED status.
9. Return ONLY JSON matching the provided schema.
"""


def build_user_prompt(
    *,
    job_payload: dict[str, Any],
    candidate_evidence: dict[str, Any],
    skill_report: dict[str, Any],
    source_content_partial: bool,
    schema: dict[str, Any],
) -> str:
    payload = {
        "task": "Produce SemanticJobEvaluation JSON for qualification analysis only.",
        "source_content_partial": source_content_partial,
        "job": job_payload,
        "candidate_evidence": candidate_evidence,
        "deterministic_skill_report": skill_report,
        "match_levels": [
            "STRONG_MATCH",
            "MATCH",
            "PARTIAL_MATCH",
            "TRANSFERABLE",
            "NO_EVIDENCE",
            "CONFLICT",
            "UNKNOWN",
        ],
        "evidence_strengths": [
            "PROFESSIONAL_EXPERIENCE",
            "PROJECT",
            "EDUCATION",
            "CERTIFICATION",
            "LISTED_SKILL",
            "UNKNOWN",
        ],
        "output_schema": schema,
        "notes": [
            "Include years-of-experience as an EXPERIENCE requirement when the job states years.",
            "Include education requirements when stated.",
            "Populate job_characteristics from the posting (development/backend/frontend/support/management focus).",
            "If source_content_partial is true, lower overall_confidence and add an uncertainty.",
        ],
    }
    return json.dumps(payload, default=str)
