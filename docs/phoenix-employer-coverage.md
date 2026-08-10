# Phoenix Employer Coverage (Phase 3.4)

Research date: 2026-08-10 (live public careers / ATS probes).

Purpose: map Phoenix-metro employers that plausibly hire software / backend / platform engineers, and determine whether their boards can be consumed by existing Discovery providers (Greenhouse, Lever, Ashby) without weakening geographic filters.

Candidate preference context (selection priority only — not claims about individual jobs): Java / Spring Boot backend, REST APIs, SQL, AWS/cloud, Kubernetes/Docker, CI/CD, enterprise/platform engineering; Chandler / Phoenix metro preferred; US remote acceptable; relocation not desired.

## Summary

| Metric | Count |
|--------|------:|
| Employers researched | 34 |
| Usable via Greenhouse (ADD NOW) | 4 (+ existing remote boards retained) |
| Usable via Lever (ADD NOW) | 1 |
| Usable via Ashby (ADD NOW) | 1 |
| Workday / Workday-shaped | 12 |
| Oracle Recruiting Cloud | 2 |
| Custom / SSO / other | 11 |
| Not useful / defer | 4 |

**Next ATS recommendation (exactly one):** **Workday** — unlocks the largest share of high-value Phoenix-metro engineering employers that existing providers cannot reach (Intel, NXP, Choice Hotels, Freeport-McMoRan, and other enterprise/semiconductor boards). Do **not** implement Workday in this phase; registry expansion uses Greenhouse/Lever/Ashby only.

## Matrix

| Employer | Metro Location | Engineering Relevance | ATS | Existing Provider? | Programmatic Access | Registry Candidate | Notes |
|---|---|---|---|---|---|---|---|
| Axon | Scottsdale | High — large SWE / platform board, many AZ roles | Greenhouse (`axon`) | Yes | Public board API | **ADD NOW** | Verified ~45 AZ-labeled eng-adjacent roles |
| Carvana | Tempe HQ | High — backend/data SWE at Tempe | Greenhouse (`carvana`) | Yes | Public board API | **ADD NOW** | Verified Tempe Senior Software Engineer + data eng |
| GoDaddy | Tempe / Phoenix | High — major AZ tech employer | Greenhouse (`godaddy`) | Yes | Public board API | **ADD NOW** | Already registered; live jobs often remote/non-AZ labeled |
| Virtuous | Phoenix | High — local SaaS, platform/DevSecOps | Ashby (`virtuous`) | Yes | Public job-board API | **ADD NOW** | Verified Phoenix Lead Data Platform Engineer, Sr DevSecOps |
| GoHighLevel | Gilbert HQ | High — product/eng org at Gilbert | Lever (`gohighlevel`) | Yes | Public postings API | **ADD NOW** | HQ local; current postings often remote-labeled |
| Waymo | Phoenix ops | Medium — ops + some eng listings include Phoenix | Greenhouse (`waymo`) | Yes | Public board API | **ADD NOW** | Sparse SWE; keep for metro coverage |
| New Relic | Multi-city incl. Phoenix | Medium — eng mgr listings include Phoenix | Greenhouse (`newrelic`) | Yes | Public board API | **ADD NOW** | Multi-city strings; filters still apply |
| Ping Identity | Remote / multi | Medium — identity platform eng | Greenhouse (`pingidentity`) | Yes | Public board API | **ADD NOW** | Weak AZ labeling today; US-remote useful |
| Intel | Chandler / Phoenix | Very high — system/firmware/SWE | Workday (`intel.wd1…`) | No | Undocumented CXS | **RESEARCHED / UNSUPPORTED ATS** | Primary semiconductor employer |
| NXP | Chandler | High — embedded/semiconductor SWE | Workday (`nxp.wd3…`) | No | Undocumented CXS | **RESEARCHED / UNSUPPORTED ATS** | |
| Microchip | Chandler | High — embedded/firmware | Custom / Workday-shaped | No | Unstable public feed | **RESEARCHED / UNSUPPORTED ATS** | careers.microchip.com |
| onsemi | Phoenix | High — semiconductor SWE | Workday-shaped | No | Fragile | **RESEARCHED / UNSUPPORTED ATS** | |
| TSMC Arizona | Phoenix metro | High — fab + IT/eng | Workday-shaped | No | Fragile / bot mgmt | **RESEARCHED / UNSUPPORTED ATS** | |
| Honeywell Aerospace | Phoenix | High — avionics / embedded SWE | Oracle Recruiting (`careers.honeywell.com`) | No | Not Greenhouse/Lever/Ashby | **RESEARCHED / UNSUPPORTED ATS** | |
| American Express | Phoenix | Very high — Java/Spring enterprise | Oracle Recruiting (`careers.americanexpress.com`) | No | Not GH/Lever/Ashby | **RESEARCHED / UNSUPPORTED ATS** | Strong backend overlap |
| Wells Fargo | Chandler | High — enterprise eng | Workday / custom | No | Unsupported | **RESEARCHED / UNSUPPORTED ATS** | |
| State Farm | Tempe | High — enterprise eng | Custom | No | Unsupported | **RESEARCHED / UNSUPPORTED ATS** | |
| USAA | Phoenix | High — enterprise eng | Custom / Workday | No | Unsupported | **RESEARCHED / UNSUPPORTED ATS** | |
| GEICO | Phoenix | Medium–high | Custom | No | Unsupported | **RESEARCHED / UNSUPPORTED ATS** | |
| Early Warning | Scottsdale | High — payments platform | Custom | No | Unsupported | **RESEARCHED / UNSUPPORTED ATS** | Zelle operator |
| Insight Enterprises | Chandler | Medium–high — IT/solutions | SSO / custom | No | Login wall | **RESEARCHED / UNSUPPORTED ATS** | |
| Choice Hotels | Phoenix HQ | Medium — corporate eng | Workday | No | CXS | **RESEARCHED / UNSUPPORTED ATS** | |
| Republic Services | Phoenix HQ | Medium — enterprise IT | Custom / Workday | No | Unsupported | **RESEARCHED / UNSUPPORTED ATS** | |
| Freeport-McMoRan | Phoenix HQ | Medium — enterprise IT/eng | Workday | No | CXS | **RESEARCHED / UNSUPPORTED ATS** | |
| SRP | Tempe | Medium — utility IT | Custom | No | Unsupported | **RESEARCHED / UNSUPPORTED ATS** | |
| APS | Phoenix | Medium — utility IT | Custom | No | Unsupported | **RESEARCHED / UNSUPPORTED ATS** | |
| Banner Health | Phoenix | Medium — healthcare IT | Workday | No | CXS | **RESEARCHED / UNSUPPORTED ATS** | |
| Mayo Clinic | Phoenix / Scottsdale | Medium — healthcare IT | Custom | No | Unsupported | **RESEARCHED / UNSUPPORTED ATS** | |
| Cox / Cox Automotive | Phoenix | Medium — telecom/auto tech | Custom / Workday | No | Unsupported | **RESEARCHED / UNSUPPORTED ATS** | |
| Offerpad | Tempe | Medium — proptech | Unknown / custom | No | Not GH/Lever/Ashby | **DEFER** | Could not verify public ATS tenant |
| PayPal | Historical Scottsdale | Medium — payments | Workday | No | CXS | **DEFER** | Weak current AZ footprint for this candidate |
| Samsara | Remote / other metros | Medium | Greenhouse | Yes | Public API | **NOT USEFUL** (local) | 0 AZ SWE; already covered by remote boards if needed |
| Lyft / Block | Remote hubs | High nationally | Greenhouse | Yes | Public API | **NOT USEFUL** (local) | Retain only as US-remote registry peers, not Phoenix |
| PetSmart / Best Buy | Phoenix retail HQ | Low–medium eng signal | Unknown | No | Unverified | **DEFER** | Large HQ ≠ verified eng board access |

## Classifications explained

### ADD NOW
Verified public Greenhouse / Lever / Ashby tenants with Phoenix-metro engineering relevance (Axon, Carvana, GoDaddy, Virtuous, GoHighLevel, Waymo, plus selective multi-city/remote boards that already fit the existing registry model).

### RESEARCHED / UNSUPPORTED ATS
Important local employers whose careers systems are Workday, Oracle Recruiting, SSO, or custom. Documented for the gap report; **not** implemented in Phase 3.4.

### NOT USEFUL
Large Greenhouse boards with no Phoenix-metro engineering inventory (for local coverage goals).

### DEFER
Plausible employers without a verified public tenant ID or weak current AZ engineering footprint.

## Gap report (Phoenix-relevant sample)

```
Phoenix employers researched: 34

Existing supported ATS (ADD NOW local-capable):
  Greenhouse: 4 core local (axon, carvana, godaddy, waymo) + selective remote boards
  Lever: 1 (gohighlevel)
  Ashby: 1 (virtuous)

Unsupported among researched local/high-value set:
  Workday / Workday-shaped: 12
  Oracle Recruiting Cloud: 2 (American Express, Honeywell)
  Custom / SSO / other: 11
  Defer / not useful: 4
```

## Next ATS recommendation

**Workday** would unlock the single largest cluster of Phoenix-metro engineering employers that Greenhouse/Lever/Ashby cannot reach today — especially semiconductor (Intel, NXP, onsemi/TSMC-shaped) and enterprise HQ boards (Choice, Freeport, Banner).

Oracle Recruiting is second-priority (Amex + Honeywell are excellent backend fits) but unlocks fewer employers in this research set than Workday.

Default for this phase: research first, expand existing providers, recommend Workday — do not implement Workday yet.

## Broad-search local queries (logical)

Planner emits consolidated Type-B requests (Muse), local first:

| Bucket | Role | Location |
|--------|------|----------|
| local | Software Engineer | Chandler, AZ |
| local | Backend Engineer | Chandler, AZ |
| local | Java Engineer | Chandler, AZ |
| local | Software Engineer | Tempe, AZ |
| local | Backend Engineer | Tempe, AZ |
| local | Java Engineer | Tempe, AZ |
| local | Software Engineer | Mesa, AZ |
| local | Backend Engineer | Mesa, AZ |
| local | Java Engineer | Mesa, AZ |
| local | Software Engineer | Gilbert, AZ |
| local | Backend Engineer | Gilbert, AZ |
| local | Java Engineer | Gilbert, AZ |
| local | Software Engineer | Scottsdale, AZ |
| local | Backend Engineer | Scottsdale, AZ |
| local | Java Engineer | Scottsdale, AZ |
| local | Software Engineer | Phoenix, AZ |
| local | Backend Engineer | Phoenix, AZ |
| local | Java Engineer | Phoenix, AZ |
| remote | Software Engineer | (category-only / none) |
| remote | (none) | (category-only) |

Cap: ≤6 local cities × ≤3 roles + ≤2 remote sweeps. No full `target_roles × cities` Cartesian product.

Locations are derived from candidate preferences (`preferred_locations` / `acceptable_locations`), normalized to `City, ST` — not hardcoded to Phoenix inside providers.
