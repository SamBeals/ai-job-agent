# Factual Integrity Rules

These rules apply to every agent that reads or transforms candidate career data
(Scout, Resume, Applicant, and any future agents).

## What agents may do

Agents may:

- select verified facts
- reorder verified facts
- summarize verified facts
- rephrase verified facts
- combine compatible verified facts
- identify relationships between verified facts

## What agents may NOT invent

Agents must never invent:

- employers
- titles
- employment dates
- education
- degrees
- certifications
- technologies
- years of experience
- metrics
- accomplishments
- project scope
- team size
- management responsibility
- security clearance
- compensation history
- proficiency levels

## Unknown information

If information is missing, it is **UNKNOWN**.

- UNKNOWN must never silently become TRUE.
- A skill listed on a résumé does **not** prove duration, depth, recency,
  expert status, or production usage.
- Prefer `null` / omitted optional fields over fabricated defaults.

## Evidence strength

When matching skills or experience, prefer stronger evidence:

1. Professional experience (explicitly demonstrated in employment history)
2. Project evidence
3. Education
4. Certification
5. Listed skill only (verified inventory, depth unknown)
6. Unknown (no verified evidence)

## Preferences vs facts

Candidate **facts** describe past career reality.

Candidate **preferences** describe what the candidate wants next.

Preferences are not career facts and must not be treated as accomplishments.
