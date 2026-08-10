# ATS Coverage Analysis (Phase 3.6)

Research date: 2026-08-10. Companion to [phoenix-employer-coverage.md](./phoenix-employer-coverage.md), [workday-research.md](./workday-research.md), and [oracle-recruiting-research.md](./oracle-recruiting-research.md). Counts are approximate but consistent with the employer matrix (74 researched).

## Scope

- Employers researched: **74**
- Focus: Chandler / Phoenix metro engineering employers + existing US-remote Type-A registry peers
- Method: live public careers / board probes; Workday/Oracle tenants listed only when public JSON returned HTTP 200 with plain `httpx`

## ATS distribution (research set)

| ATS / access class | Employers (approx.) | Notes |
|--------------------|--------------------:|-------|
| Greenhouse | 18 | All registry-active |
| Lever | 4 | All registry-active |
| Ashby | 11 | All registry-active |
| Workday (CXS verified) | 9 | Phase 3.5 |
| Oracle Recruiting (CE verified) | **2** | Phase 3.6 — Amex, Honeywell |
| Workday (HTML OK, CXS 422) | 4 | Freeport, Banner, RTX, Wells Fargo — not registered |
| Workday-shaped / fragile | 3 | Microchip, onsemi, TSMC — no verified triple |
| Custom / SSO / other | 17 | State Farm, GEICO, Early Warning, utilities, etc. |
| Unknown / defer | 6 | Offerpad, PetSmart, Best Buy, Blue Yonder, GCE, Schwab |
| **Total** | **74** | |

```
Supported Type-A ATS in registry today: Greenhouse + Lever + Ashby + Workday + Oracle = 44 employers
Unsupported / defer in research set: 30
Estimated direct coverage: 44 / 74 ≈ 59%
```

## Already supported vs newly unlocked vs still unsupported

| Bucket | Count | What it means |
|--------|------:|---------------|
| Already supported (pre-Oracle) | 42 | GH 18 + Lever 4 + Ashby 11 + Workday 9 |
| Newly unlocked by Oracle | **2** | American Express, Honeywell |
| Still unsupported / defer | **30** | unresolved WD (4) + WD-shaped (3) + custom/SSO (17) + defer (6) |

Oracle unlocks only **two** employers in this matrix — both high-signal Phoenix fits, not a broad ATS sweep.

## Provider capability snapshot

| Provider | Status in product | Public feed quality | Geo utility | Phoenix unlock value |
|----------|-------------------|---------------------|------------|----------------------|
| Greenhouse | Existing | High (documented board API) | Location strings | Strong local + remote peers |
| Lever | Existing | High | Site-scoped | GoHighLevel + remote peers |
| Ashby | Existing | High (+ workplace type) | Board-scoped | Virtuous + remote peers |
| Workday CXS | Phase 3.5 | Good when triad correct | Facets / searchText | Enterprise / semi cluster |
| Oracle CE | **Phase 3.6** | Good when host+siteNumber verified; undocumented CE | Keyword + optional geography facet | Amex Phoenix SWE; Honeywell thinner |
| The Muse (Type B) | Existing | Noisy but geo-capable | Strong | Complements Type A |
| SmartRecruiters | Not implemented | Documented when plan enables public postings | City/country filters | Best remaining *feasibility × quality* |
| iCIMS | Not implemented | Partner / HTML-heavy | Poor as multi-tenant public API | Low near-term feasibility |
| Jobvite | Not implemented | Customer APIs | Poor multi-tenant public feed | Low near-term feasibility |
| Custom / SSO | N/A | Per-employer | Varies | Defer unless a clean public JSON appears |

## Next provider prioritization

Score axes (1–5): **coverage** (how many high-value employers in this research set), **feasibility** (documented or probe-friendly public JSON without bot evasion), **data quality** (descriptions, stable IDs, apply URLs for Scout).

| Priority | ATS | Coverage | Feasibility | Data quality | Composite | Rationale |
|----------|-----|----------|-------------|--------------|-----------|-----------|
| **1 (recommend)** | **SmartRecruiters** | 3 | 4 | 4 | **11** | Documented public postings API when enabled; strong structured shape. Best remaining feasibility after Oracle is done. |
| 2 | Custom (selective) | 5 | 1–2 | 2 | ~8 | Largest residual pile (State Farm, Early Warning, utilities, etc.) but each board is a one-off. |
| 3 | Additional Workday variants | 2 | 2 | 3 | 7 | Freeport/Banner/RTX/Wells only if CXS site paths verify — not a new ATS. |
| 4 | iCIMS | 2 | 1 | 2 | 5 | Common enterprise ATS; no clean multi-tenant public feed. |
| 5 | Jobvite | 1 | 1 | 2 | 4 | Customer APIs; not suitable as a first-class provider now. |

## Recommended next ATS: SmartRecruiters

**Choose SmartRecruiters** as the single next Type-A provider after Oracle.

Why:

1. **Oracle is done** for the two verified CE employers in this set (Amex + Honeywell). Remaining Oracle-shaped employers were not found in the 74-employer matrix.
2. **Feasibility × data quality:** SmartRecruiters has a documented public postings path when companies enable it — higher predictability than reverse-engineering another proprietary SPA.
3. **Coverage path:** Grow a curated list of SR company IDs with Phoenix/backend fit rather than chasing SSO/custom careers one-by-one.
4. **Do not** expand unresolved Workday 422 boards until site paths verify — that is registry hygiene, not a new provider.

Quantitative sketch for this research set:

- Oracle gain: **+2** employers → direct coverage 42→44 (~57%→~59%).
- SmartRecruiters next: expect smaller employer count than Workday’s 9, but better than another undocumented CE-like integration when Amex/Honeywell are already unlocked.

Do **not** expand the Workday registry with Freeport / Banner / RTX / Wells Fargo until CXS triples are verified.

## US remote employer opportunities (brief)

Remote Type-A boards already in the registry cover most US-remote discovery needs for this candidate.

Guidance:

- Prefer **filling remaining Phoenix metro gaps** (custom boards with verified JSON; later SmartRecruiters company IDs) over mass-adding more remote Greenhouse/Ashby logos.
- Keep Type-B Muse remote sweeps for serendipity; do not treat Muse landing URLs as a substitute for employer ATS registry growth.

## Compliance reminders

- Register only verified public feeds (GH board token, Lever/Ashby slug, Workday host/tenant/site, Oracle host/site_number/site_path/career_base_url).
- No LinkedIn/Indeed HTML scraping, CAPTCHA bypass, or credential extraction.
- Isolate per-board failures; disable boards that start requiring anonymous challenges.

## Related docs

- [phoenix-employer-coverage.md](./phoenix-employer-coverage.md) — full employer matrix
- [workday-research.md](./workday-research.md) — Workday CXS architecture
- [oracle-recruiting-research.md](./oracle-recruiting-research.md) — Oracle CE architecture and decision
- [discovery-provider-research.md](./discovery-provider-research.md) — Phase 3.3 provider landscape
