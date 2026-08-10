# Workday Public Career Architecture (Phase 3.5 research)

Research date: 2026-08-10. Method: live probes with plain `httpx` (no CAPTCHA bypass, proxies, TLS impersonation, or credential extraction).

## Verdict

**Workday integration is appropriate** for Discovery.

Public Workday career sites expose the same unauthenticated JSON endpoints the careers UI uses. Several Phoenix-relevant tenants respond successfully to ordinary HTTP without anti-bot evasion. Failures are typically **wrong tenant/site triples (HTTP 422)** or transient 502s — not a requirement to evade bot management for the boards we verified.

## Endpoint shape

```
POST https://{host}/wday/cxs/{tenant}/{site}/jobs
Content-Type: application/json

{
  "appliedFacets": {},
  "limit": 20,
  "offset": 0,
  "searchText": "software engineer"
}
```

Detail (optional, per posting):

```
GET https://{host}/wday/cxs/{tenant}/{site}{externalPath}
```

Public job URL (when `externalUrl` absent):

```
https://{host}/{site}{externalPath}
```

### Identifier triad

| Part | Example | Notes |
|------|---------|-------|
| host | `intel.wd1.myworkdayjobs.com` | Includes datacenter (`wd1`/`wd3`/`wd5`/`wd12`/…) |
| tenant | `intel` | Usually host label; case-sensitive on some tenants |
| site | `External` | Per-customer slug — cannot be guessed reliably |

Official Workday REST/SOAP staffing APIs require tenant OAuth and are **not** used.

## What the JSON contains

| Field | Listing | Detail |
|-------|---------|--------|
| title | yes | yes |
| locationsText / location | yes | yes |
| externalPath | yes | — |
| postedOn / startDate | often relative on list; ISO on detail | yes |
| jobDescription (HTML) | no | yes |
| jobReqId | sometimes in path | yes |
| job posting id | — | yes (`id` / `jobPostingId`) |
| externalUrl | — | often yes |
| remoteType / timeType | rare | sometimes |
| compensation | rare / absent | usually absent → unknown |

Auth: **none** for these public CXS reads.

Pagination: `limit`/`offset`; practical page size ~20; bound pages in provider. Some tenants advertise large `total` values — Discovery must cap.

## Live probe outcomes (plain httpx)

| Employer | Triple | CXS | Notes |
|----------|--------|-----|-------|
| Intel | intel.wd1 / intel / External | 200 | AZ Phoenix SWE present via searchText |
| NXP | nxp.wd3 / nxp / careers | 200 | Chandler presence historically |
| Choice Hotels | choicehotels.wd5 / choicehotels / External | 200 | Multiple Scottsdale AZ SWE roles |
| USAA | usaa.wd1 / usaa / USAAJOBSWD | 200 | Phoenix-relevant enterprise |
| PayPal | paypal.wd1 / paypal / jobs | 200 | US remote/enterprise |
| Northrop Grumman | ngc.wd1 / ngc / Northrop_Grumman_External_Site | 200 | Defense SWE |
| Boeing | boeing.wd1 / boeing / EXTERNAL_CAREERS | 200 | Enterprise SWE |
| Capital One | capitalone.wd12 / capitalone / Capital_One | 200 | Large eng org |
| Target | target.wd5 / target / targetcareers | 200 | Enterprise eng |
| Freeport / Banner / RTX / Wells Fargo | careers HTML 200 | **422** on tried CXS paths | Site path unresolved — **not** registered |

## Compliance posture

- Consume only public career JSON the site itself loads.
- No login automation, CAPTCHA bypass, proxy rotation, or fingerprint spoofing.
- Registry contains **verified** host/tenant/site only.
- Per-board failure isolation; polite User-Agent; bounded pagination + timeouts.
- If a tenant starts requiring challenges for anonymous CXS, disable that board — do not evade.

## Provider implication

Implement `WorkdayDiscoveryProvider` behind existing `DiscoveryProvider`, registry-driven, returning `RawDiscoveryResult`. Prefer detail fetch for description preservation so Scout can use structured content.
