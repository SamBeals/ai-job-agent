# Discovery Provider Research (Phase 3.3)

Research date: 2026-08-10. Focus: legitimate programmatic access for US software-engineering discovery, especially Chandler / Phoenix metro, without LinkedIn/Indeed HTML scraping.

## Matrix

| Provider | Programmatic Access | Global Search | Geo Search | Description | Salary | Canonical URL | Auth | Cost | Recommendation |
|----------|---------------------|---------------|------------|-------------|--------|---------------|------|------|----------------|
| Greenhouse Job Board API | Documented public JSON per board | No (tenant tokens required) | Location strings only; no geo query | HTML content via `?content=true` | Rare | `absolute_url` | None | Free | **EXISTING** |
| Remotive public API | Documented public JSON | Remote software category feed | Remote-only | Yes | Text often | Job URL | None | Free | **EXISTING** |
| Lever Postings API | Documented public JSON `GET /v0/postings/{site}?mode=json` | No (site slug required) | Filter params exist; no metro index | Plain + HTML | No | `hostedUrl` / `applyUrl` | None (public read) | Free | **RECOMMENDED NOW** |
| Ashby Job Postings API | Documented public JSON `GET /posting-api/job-board/{name}` | No (board name required) | Location + workplaceType fields | Plain + HTML | Often via `includeCompensation=true` | `jobUrl` / `applyUrl` | None (public read) | Free | **RECOMMENDED NOW** |
| The Muse Jobs API | Public JSON search API | Yes (keyword/category/location pages) | Yes (`location=Phoenix, AZ` etc.) | HTML `contents` | No | Muse landing page (not always employer ATS URL) | None for public | Free (rate limits) | **RECOMMENDED NOW** (Type B) |
| Adzuna Jobs API | Documented REST search | Yes | Yes (`where=Chandler`) | Snippet / description | Often | `redirect_url` | `app_id` + `app_key` | Free tier (25/min, 250/day) + attribution rules | **PROMISING LATER / optional** |
| SmartRecruiters Postings API | Documented public JSON per company | No | Query filters (`city`, `country`) when enabled | Detail endpoint needed for full body | Limited | refs / apply URLs | None when plan enables public feed | Free when enabled | **PROMISING LATER** |
| Workday CXS | Undocumented careers JSON (`POST .../wday/cxs/.../jobs`) | No | Facets vary by tenant | Second request per job | Rare | Constructed careers URL | None on public boards | Free but fragile | **PROMISING LATER** |
| Recruitee / Breezy / BambooHR | Company-specific public JSON | No | Varies | Varies | Rare | Usually | None | Free | **PROMISING LATER** |
| iCIMS | Customer/partner APIs; public board HTML common | No useful global API | — | — | — | — | Usually partner | — | **NOT SUITABLE** (now) |
| Jobvite | Customer APIs; not a clean multi-tenant public feed | No | — | — | — | — | Customer | — | **NOT SUITABLE** (now) |
| JSearch / RapidAPI aggregators | Commercial wrappers over Google-for-Jobs etc. | Yes | Yes | Yes | Estimates | Mixed | API key | Paid after free tier | **PROMISING LATER** (cost/TOS review) |
| LinkedIn / Indeed / Glassdoor / ZipRecruiter HTML | Scraping only for practical access | — | — | — | — | — | — | — | **NOT SUITABLE** (prohibited for this project) |

## Classifications

### RECOMMENDED NOW

1. **Lever** — Official Postings API, stable IDs, hosted + apply URLs, structured descriptions. Same Type A pattern as Greenhouse (employer registry of site slugs). No global company discovery.
2. **Ashby** — Official public job-board API, excellent descriptions, workplace type, optional compensation. Strong tech-employer coverage. Registry of board names required.
3. **The Muse** — Only no-key Type B option verified live with `location=Chandler, AZ` / `Phoenix, AZ` returning real local software-adjacent postings (e.g. Bank of America Chandler SRE). Noisy (drivers, mechanical) — existing Discovery filters must remain. Landing URLs are Muse-hosted; structured `contents` still enables SCOUT THIS without a second fetch.

### PROMISING LATER

- **Adzuna** — Best geo search quality among legitimate APIs, but requires signup keys + “Jobs by Adzuna” attribution if published. Implement adapter behind enable flag; keep off by default until keys + attribution UX are ready.
- **SmartRecruiters** — Good when company enables public postings; plan-dependent availability.
- **Workday CXS** — Critical for large AZ employers (Intel, etc.) but undocumented, tenant/site/datacenter triad, pagination traps, bot management. Defer until registry of known careers URLs is curated carefully.
- **JSearch** — Broad coverage; revisit after TOS/cost review (aggregates boards we refuse to scrape directly).

### NOT SUITABLE (now)

- LinkedIn / Indeed / Glassdoor / ZipRecruiter HTML scraping, CAPTCHA bypass, stealth browsers.
- iCIMS / Jobvite as first-class providers without a clean public multi-tenant feed.

## Live probe notes (2026-08-10)

- Lever `palantir`, `spotify`, `zoox` returned JSON postings; many historical Lever brands now 404 (moved ATS).
- Ashby `notion` returned 127 jobs with descriptions + workplaceType.
- Muse `Software Engineering` + `Chandler, AZ` returned Chandler Bank of America infrastructure/SRE roles mixed with non-targets.
- Greenhouse `godaddy` returned jobs (AZ-relevant employer to add to registry).

## Selected for Phase 3.3 implementation

| Provider | Role |
|----------|------|
| Lever | Type A ATS expansion |
| Ashby | Type A ATS expansion |
| The Muse | Type B broad geo search (free) |
| Adzuna | Type B optional; disabled without credentials |
| Employer board registry | `config/discovery_boards.json` for Greenhouse/Lever/Ashby tenants |

Filters, ranking, `DISCOVERY_MIN_SURFACE_SCORE=45`, US-only rules, and cross-run dedupe remain unchanged in spirit; messaging distinguishes zero-quality vs all-previously-seen.
