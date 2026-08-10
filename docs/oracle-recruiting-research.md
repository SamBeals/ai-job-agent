# Oracle Recruiting / Candidate Experience (Phase 3.6 research)

Research date: 2026-08-10. Method: live probes with plain `httpx` against public career sites and Oracle Cloud HCM hosts (no CAPTCHA bypass, proxies, TLS impersonation, session theft, or private credential extraction).

## Verdict

**IMPLEMENTATION DECISION: SUPPORTED**

American Express and Honeywell both expose Oracle Recruiting **Candidate Experience (CE)** public REST JSON used by their careers SPAs. Ordinary anonymous `GET` requests to `{oracle_host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions` with the `findReqs` finder return structured requisition lists. Detail endpoints return full HTML descriptions. No API key is required for these public CE reads.

Oracle Fusion docs mark some CE resources as “for Oracle internal use,” but the same endpoints are what the public careers UI calls. Access posture matches Phase 3.5 Workday CXS: use only verified public endpoints, bound pagination, and register only employers whose host/site we re-verified.

## 1. Architecture observed

Modern employer career sites are Oracle Cloud HCM **Candidate Experience** (often “CE” / CandExp):

| Layer | Role |
|-------|------|
| Branded career host | e.g. `careers.americanexpress.com`, `careers.honeywell.com` — SPA shell + vanity URLs |
| Oracle FA host | e.g. `egug.fa.us2.oraclecloud.com`, `ibqbjb.fa.ocs.oraclecloud.com` — REST JSON |
| Site number | Public site id, commonly `CX_1` |
| Site path | URL slug under `/en/sites/{path}/` — may equal site number (Amex) or a brand slug (Honeywell → `Honeywell`) |

Custom career domains **do not** usefully proxy REST (Amex career host returns HTML for `/hcmRestApi/...`). Discovery must call the **Oracle FA host** for JSON and the **career base URL** for canonical job links.

## 2. Public career URL patterns

```
https://{career_base}/en/sites/{site_path}
https://{career_base}/en/sites/{site_path}/job/{requisitionId}
```

Verified:

| Employer | Career base | Site path | Example job URL |
|----------|-------------|-----------|-----------------|
| American Express | `https://careers.americanexpress.com` | `CX_1` | `.../en/sites/CX_1/job/26011927` |
| Honeywell | `https://careers.honeywell.com` | `Honeywell` | `.../en/sites/Honeywell/job/{id}` |

Honeywell also accepts `/en/sites/CX_1/job/{id}` (200); prefer the branded `Honeywell` path from the registry.

## 3. Structured endpoint patterns

Search (anonymous JSON):

```
GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions
  ?onlyData=true
  &expand=requisitionList.workLocation,requisitionList.otherWorkLocations,requisitionList.secondaryLocations
  &finder=findReqs;siteNumber={siteNumber},keyword={kw},limit={n},offset={o}
```

`expand` is required for `requisitionList` to populate under `onlyData=true` (empty list otherwise).

Detail (full description):

```
GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails
  ?onlyData=true
  &finder=ById;Id={requisitionId},siteNumber={siteNumber}
```

Sites metadata (optional):

```
GET .../recruitingCESites?finder=SiteByNumberFinder;SiteNumber=CX_1&onlyData=true
```

Auth: **none** for these public CE reads.

## 4. Tenant / site identification

| Field | Amex | Honeywell |
|-------|------|-----------|
| Oracle host | `egug.fa.us2.oraclecloud.com` | `ibqbjb.fa.ocs.oraclecloud.com` |
| siteNumber | `CX_1` | `CX_1` |
| site path (URL) | `CX_1` | `Honeywell` |
| SiteName (API) | American Express | (via careers UI) |

HTML attributes on career pages include `data-sitenumber="CX_1"`. Favicon/image URLs on the branded host reveal the Oracle FA host.

## 5. Search request format

Finder variables used successfully:

- `siteNumber` (required)
- `keyword` (optional; empty returns broad inventory)
- `limit` / `offset` (pagination inside finder)
- `selectedLocationsFacet` + `lastSelectedFacet=LOCATIONS` (optional geography id)

Plain `Location=Phoenix` in the finder returned **HTTP 400** — do not use free-text Location finder vars without verification.

## 6. Pagination

- Outer ADF collection usually returns one search “item”; jobs live in `requisitionList`.
- Page via finder `limit`/`offset` (e.g. 25).
- `TotalJobsCount` on the search item indicates completion when `offset + len(list) >= TotalJobsCount`.
- Bound max pages / max jobs in the provider; never exhaust large corporate catalogs.

## 7. Detail retrieval

`recruitingCEJobRequisitionDetails` with `ById` returns:

- `ExternalDescriptionStr`, `ExternalResponsibilitiesStr`, `ExternalQualificationsStr` (HTML)
- location / workplace fields
- **no salary/compensation keys** on Amex sample (2026-08-10)

Preserve descriptions for Scout; leave salary unknown when absent.

## 8. Location data

| Field | Notes |
|-------|-------|
| `PrimaryLocation` | e.g. `Phoenix, AZ, United States` |
| `PrimaryLocationCountry` | e.g. `US` |
| `GeographyId` | numeric; usable as `selectedLocationsFacet` (employer-specific) |
| `workLocation[]` | town/city, region, country, lat/long |

Amex Phoenix geography example: `300000007667464` (narrows software search usefully).  
Honeywell Phoenix geography example: `300000007395144` — facet can return **mixed** cities; treat as retrieval bias only; Discovery geo filters remain authoritative.

## 9. Description quality

Listings often have empty `ShortDescriptionStr`. Detail endpoint supplies rich HTML suitable for Scout structured content (strip HTML → plain text). Prefer detail fetch for title-matched candidates within a bound.

## 10. Stable identifiers

- Public requisition `Id` (string, e.g. `26011927`) — use as `external_id` and `requisition_id`.
- Canonical URL includes that id under `/job/{Id}`.

## 11. Canonical / application URLs

- Canonical: `{career_base}/en/sites/{site_path}/job/{Id}`
- No separate public apply URL in CE JSON samples → `job_url` = canonical.

## 12. Rate / access behavior

- Plain httpx succeeded for both employers without challenges.
- Respect timeouts; treat 429 as failure for that board (isolated).
- Career-host REST paths return SPA HTML — not usable as API.

## 13. Employer differences

| Employer | Phoenix SWE signal | Notes |
|----------|-------------------|-------|
| American Express | Strong | Multiple Phoenix hybrid Software Engineer roles via keyword + optional location facet |
| Honeywell | Weaker for classic SWE | Large global catalog; Phoenix hits skew cyber/IT/ops; still valid Oracle tenant |

Only these two were classified Oracle in the Phase 3.5 Phoenix matrix; both re-verified as Oracle CE on 2026-08-10.

## 14. Technical risks

- Undocumented / “internal” CE resource labeling — endpoints can change with Fusion releases (`latest` vs versioned path).
- Wrong `siteNumber` → empty results (not always hard error).
- Location facets are not portable across tenants.
- Honeywell volume requires strict caps.
- Custom career domains must not be used as REST hosts.

## 15. Final implementation decision

**IMPLEMENTATION DECISION: SUPPORTED**

Public anonymous CE JSON is sufficient for a Type-A Discovery provider analogous to Workday CXS. Register only verified Amex + Honeywell boards. Prefer keyword (+ optional registered location facet ids) with bounded pages; never scrape HTML or evade bot controls.
