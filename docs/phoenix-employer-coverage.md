# Phoenix Employer Coverage (Phase 3.6)

Research date: 2026-08-10 (live public careers / ATS probes; Workday CXS + Oracle CE verified where listed).

Purpose: map Phoenix-metro and high-value US-remote employers that plausibly hire software / backend / platform engineers, and record whether Discovery can consume their boards via existing providers (Greenhouse, Lever, Ashby, Workday, Oracle) without inventing unverified tenants or weakening geographic filters.

Candidate preference context (selection priority only — not claims about individual jobs): Java / Spring Boot backend, REST APIs, SQL, AWS/cloud, Kubernetes/Docker, CI/CD, enterprise/platform engineering; Chandler / Phoenix metro preferred; US remote acceptable; relocation not desired.

## Summary

| Metric | Count |
|--------|------:|
| Employers researched | 74 |
| Registry **active** (GH / Lever / Ashby / Workday / Oracle) | **44** |
| Directly supported before Oracle | 42 |
| Newly supported by Oracle | **2** (American Express, Honeywell) |
| Remaining unsupported / defer | **30** |
| Greenhouse (registry) | 18 |
| Lever (registry) | 4 |
| Ashby (registry) | 11 |
| Workday CXS (registry) | 9 |
| Oracle CE (registry) | **2** |
| **researched-unsupported** | 24 |
| **defer** | 6 |
| Workday HTML OK / CXS 422 unresolved | 4 |

**Phase 3.6 change:** Oracle Recruiting Candidate Experience is implemented. Verified public CE JSON for American Express and Honeywell only. See [oracle-recruiting-research.md](./oracle-recruiting-research.md).

**Next ATS recommendation (exactly one):** **SmartRecruiters** — see [ats-coverage-analysis.md](./ats-coverage-analysis.md).

## Matrix

Columns: **Registry status** is `active` | `researched-unsupported` | `defer`. **Tenant/site** lists only verified Greenhouse board tokens, Lever/Ashby slugs, Workday host/tenant/site triples, or Oracle host/site_number/site_path — never guessed IDs.

| Employer | Phoenix presence | Engineering relevance | Careers URL | ATS | Tenant/site | Existing provider? | Registry status | Notes |
|---|---|---|---|---|---|---|---|---|
| Axon | Scottsdale HQ | High — SWE / platform | https://www.axon.com/careers | Greenhouse | `axon` | Yes | active | Large AZ-labeled eng board |
| Carvana | Tempe HQ | High — backend / data SWE | https://www.carvana.com/careers | Greenhouse | `carvana` | Yes | active | Tempe SWE + data eng |
| GoDaddy | Tempe / Phoenix | High — major AZ tech | https://careers.godaddy.com | Greenhouse | `godaddy` | Yes | active | Live jobs often remote-labeled |
| Waymo | Phoenix ops | Medium — ops + some eng | https://careers.withwaymo.com | Greenhouse | `waymo` | Yes | active | Sparse SWE; metro coverage |
| Virtuous | Phoenix | High — local SaaS / platform | https://job-boards.ashbyhq.com/virtuous | Ashby | `virtuous` | Yes | active | Phoenix platform / DevSecOps |
| GoHighLevel | Gilbert HQ | High — product eng | https://jobs.lever.co/gohighlevel | Lever | `gohighlevel` | Yes | active | HQ local; postings often remote |
| New Relic | Multi-city incl. Phoenix | Medium — eng / eng mgr | https://newrelic.com/careers | Greenhouse | `newrelic` | Yes | active | Multi-city strings; filters apply |
| Ping Identity | Remote / multi | Medium — identity platform | https://www.pingidentity.com/en/company/careers.html | Greenhouse | `pingidentity` | Yes | active | Weak AZ labeling; US-remote useful |
| Intel | Chandler / Phoenix | Very high — system / firmware / SWE | https://intel.wd1.myworkdayjobs.com/External | Workday | `intel.wd1` / `intel` / `External` | Yes | active | Verified CXS 200; metro phoenix |
| NXP | Chandler | High — embedded / semi SWE | https://nxp.wd3.myworkdayjobs.com/careers | Workday | `nxp.wd3` / `nxp` / `careers` | Yes | active | Verified CXS; metro chandler |
| Choice Hotels | Scottsdale / Phoenix HQ | Medium–high — corporate eng | https://choicehotels.wd5.myworkdayjobs.com/External | Workday | `choicehotels.wd5` / `choicehotels` / `External` | Yes | active | Verified CXS; Scottsdale SWE |
| USAA | Phoenix | High — enterprise eng | https://usaa.wd1.myworkdayjobs.com/USAAJOBSWD | Workday | `usaa.wd1` / `usaa` / `USAAJOBSWD` | Yes | active | Verified CXS; metro phoenix |
| PayPal | Historical Scottsdale; US remote | Medium–high — payments eng | https://paypal.wd1.myworkdayjobs.com/jobs | Workday | `paypal.wd1` / `paypal` / `jobs` | Yes | active | Verified CXS; metro null |
| Northrop Grumman | Defense / multi (AZ footprint) | High — defense SWE | https://ngc.wd1.myworkdayjobs.com/Northrop_Grumman_External_Site | Workday | `ngc.wd1` / `ngc` / `Northrop_Grumman_External_Site` | Yes | active | Verified CXS |
| Boeing | Multi (AZ aerospace footprint) | High — enterprise / aero SWE | https://boeing.wd1.myworkdayjobs.com/EXTERNAL_CAREERS | Workday | `boeing.wd1` / `boeing` / `EXTERNAL_CAREERS` | Yes | active | Verified CXS |
| Capital One | Multi / US remote | High — large eng org | https://capitalone.wd12.myworkdayjobs.com/Capital_One | Workday | `capitalone.wd12` / `capitalone` / `Capital_One` | Yes | active | Verified CXS |
| Target | Multi / enterprise | Medium–high — enterprise eng | https://target.wd5.myworkdayjobs.com/targetcareers | Workday | `target.wd5` / `target` / `targetcareers` | Yes | active | Verified CXS |
| Stripe | US remote | High — payments / platform | https://stripe.com/jobs | Greenhouse | `stripe` | Yes | active | Remote registry peer |
| Datadog | US remote | High — observability / backend | https://careers.datadoghq.com | Greenhouse | `datadog` | Yes | active | Remote registry peer |
| Cloudflare | US remote | High — edge / platform | https://www.cloudflare.com/careers/ | Greenhouse | `cloudflare` | Yes | active | Remote registry peer |
| GitLab | US remote | High — DevOps / product eng | https://about.gitlab.com/jobs/ | Greenhouse | `gitlab` | Yes | active | Remote registry peer |
| HashiCorp | US remote | High — infra / platform | https://www.hashicorp.com/careers | Greenhouse | `hashicorp` | Yes | active | Remote registry peer |
| Twilio | US remote | High — communications platform | https://www.twilio.com/company/jobs | Greenhouse | `twilio` | Yes | active | Remote registry peer |
| Airbnb | US remote | High — marketplace / backend | https://careers.airbnb.com | Greenhouse | `airbnb` | Yes | active | Remote registry peer |
| Discord | US remote | High — consumer / platform | https://discord.com/careers | Greenhouse | `discord` | Yes | active | Remote registry peer |
| Block | US remote | High — fintech / payments | https://block.xyz/careers | Greenhouse | `block` | Yes | active | Remote registry peer |
| Affirm | US remote | High — fintech backend | https://www.affirm.com/careers | Greenhouse | `affirm` | Yes | active | Remote registry peer |
| Chime | US remote | High — fintech | https://www.chime.com/careers/ | Greenhouse | `chime` | Yes | active | Remote registry peer |
| SoFi | US remote | High — fintech | https://www.sofi.com/careers/ | Greenhouse | `sofi` | Yes | active | Remote registry peer |
| Palantir | US remote / multi | High — platform / govtech | https://www.palantir.com/careers/ | Lever | `palantir` | Yes | active | Remote registry peer |
| Spotify | US remote / multi | High — backend / data | https://www.lifeatspotify.com | Lever | `spotify` | Yes | active | Remote registry peer |
| Zoox | Multi / AV | Medium–high — autonomy SWE | https://zoox.com/careers/ | Lever | `zoox` | Yes | active | Remote / AV peer |
| Notion | US remote | High — product / platform | https://job-boards.ashbyhq.com/notion | Ashby | `notion` | Yes | active | Remote registry peer |
| Linear | US remote | High — product eng | https://job-boards.ashbyhq.com/linear | Ashby | `linear` | Yes | active | Remote registry peer |
| Ramp | US remote | High — fintech | https://job-boards.ashbyhq.com/ramp | Ashby | `ramp` | Yes | active | Remote registry peer |
| OpenAI | US remote | High — ML / platform | https://job-boards.ashbyhq.com/openai | Ashby | `openai` | Yes | active | Remote registry peer |
| Cursor | US remote | High — developer tools | https://job-boards.ashbyhq.com/cursor | Ashby | `cursor` | Yes | active | Remote registry peer |
| Supabase | US remote | High — backend / platform | https://job-boards.ashbyhq.com/supabase | Ashby | `supabase` | Yes | active | Remote registry peer |
| Ashby | US remote | Medium–high — ATS product | https://job-boards.ashbyhq.com/ashby | Ashby | `ashby` | Yes | active | Remote registry peer |
| Perplexity | US remote | High — search / AI | https://job-boards.ashbyhq.com/perplexity | Ashby | `perplexity` | Yes | active | Remote registry peer |
| Amplitude | US remote | High — analytics platform | https://job-boards.ashbyhq.com/amplitude | Ashby | `amplitude` | Yes | active | Remote registry peer |
| Benchling | US remote | Medium–high — life-sci SaaS | https://job-boards.ashbyhq.com/benchling | Ashby | `benchling` | Yes | active | Remote registry peer |
| American Express | Phoenix | Very high — Java / Spring enterprise | https://careers.americanexpress.com | Oracle Recruiting | `egug.fa.us2` / `CX_1` / path `CX_1` | Yes | active | Verified CE JSON; Phoenix hybrid SWE present |
| Honeywell Aerospace | Phoenix | High — avionics / embedded / cyber SWE | https://careers.honeywell.com | Oracle Recruiting | `ibqbjb.fa.ocs` / `CX_1` / path `Honeywell` | Yes | active | Verified CE JSON; Phoenix inventory thinner for classic SWE |
| Freeport-McMoRan | Phoenix HQ | Medium — enterprise IT / eng | https://www.freeportmcmooran.com/careers | Workday | unresolved | No | researched-unsupported | Careers HTML 200; CXS 422 on tried paths |
| Banner Health | Phoenix | Medium — healthcare IT | https://www.bannerhealth.com/careers | Workday | unresolved | No | researched-unsupported | Careers HTML 200; CXS 422 |
| RTX | Multi (AZ defense footprint) | High — defense / aero SWE | https://careers.rtx.com | Workday | unresolved | No | researched-unsupported | Careers HTML 200; CXS 422 |
| Wells Fargo | Chandler | High — enterprise eng | https://www.wellsfargojobs.com | Workday | unresolved | No | researched-unsupported | Careers HTML 200; CXS 422 |
| Microchip | Chandler | High — embedded / firmware | https://careers.microchip.com | Custom / Workday-shaped | — | No | researched-unsupported | Unstable public feed; no verified CXS triple |
| onsemi | Phoenix | High — semiconductor SWE | https://www.onsemi.com/careers | Workday-shaped | — | No | researched-unsupported | Fragile; not registered |
| TSMC Arizona | Phoenix metro | High — fab + IT / eng | https://www.tsmc.com/english/careers | Workday-shaped | — | No | researched-unsupported | Fragile / bot mgmt; not registered |
| Amkor Technology | Chandler / Tempe | Medium–high — semi packaging / IT | https://www.amkor.com/careers/ | Custom | — | No | researched-unsupported | Chandler semis cluster |
| First Solar | Tempe / Perrysburg multi | Medium — manufacturing IT / eng | https://www.firstsolar.com/en/Careers | Custom | — | No | researched-unsupported | Tempe-area presence |
| State Farm | Tempe | High — enterprise eng | https://www.statefarm.com/careers | Custom | — | No | researched-unsupported | Large Tempe campus |
| GEICO | Phoenix | Medium–high — insurance eng | https://www.geico.com/careers/ | Custom | — | No | researched-unsupported | Phoenix footprint |
| Early Warning | Scottsdale | High — payments platform | https://www.earlywarning.com/careers | Custom | — | No | researched-unsupported | Zelle operator |
| DriveTime | Tempe | Medium — auto-finance eng | https://www.drivetime.com/careers | Custom | — | No | researched-unsupported | Local HQ; eng volume varies |
| Insight Enterprises | Chandler | Medium–high — IT / solutions | https://www.insight.com/en_US/about/careers.html | SSO / custom | — | No | researched-unsupported | Login wall |
| SRP | Tempe | Medium — utility IT | https://www.srpnet.com/careers | Custom | — | No | researched-unsupported | Utility IT |
| APS | Phoenix | Medium — utility IT | https://www.aps.com/en/About/Careers | Custom | — | No | researched-unsupported | Utility IT |
| Republic Services | Phoenix HQ | Medium — enterprise IT | https://www.republicservices.com/careers | Custom / Workday-shaped | — | No | researched-unsupported | No verified CXS triple |
| Mayo Clinic | Phoenix / Scottsdale | Medium — healthcare IT | https://jobs.mayoclinic.org | Custom | — | No | researched-unsupported | Healthcare IT |
| Cox / Cox Automotive | Phoenix | Medium — telecom / auto tech | https://jobs.coxenterprises.com | Custom | — | No | researched-unsupported | Telecom + Cox Auto tech |
| Lumen | Phoenix footprint | Medium — telecom / network eng | https://jobs.lumen.com | Custom | — | No | researched-unsupported | Legacy CenturyLink footprint |
| Amazon (AZ ops) | Phoenix metro logistics | Medium — ops-heavy; some SWE | https://www.amazon.jobs | Custom (Amazon Jobs) | — | No | researched-unsupported | Fulfillment-heavy; not GH/Lever/Ashby/WD |
| Bank of America | Chandler ops | Medium–high — infra / SRE / eng | https://careers.bankofamerica.com | Custom / enterprise ATS | — | No | researched-unsupported | Appears in Muse geo searches; no Type-A tenant |
| UnitedHealthcare / Optum | Phoenix metro | Medium — healthcare IT | https://careers.unitedhealthgroup.com | Custom | — | No | researched-unsupported | Healthcare IT volume varies |
| Avnet | Phoenix HQ | Medium — electronics / IT | https://www.avnet.com/wps/portal/us/careers | Custom | — | No | researched-unsupported | Distribution HQ; eng signal moderate |
| Offerpad | Tempe | Medium — proptech | https://www.offerpad.com/careers/ | Unknown / custom | — | No | defer | Could not verify public ATS tenant |
| PetSmart | Phoenix retail HQ | Low–medium eng signal | https://careers.petsmart.com | Unknown | — | No | defer | Large HQ ≠ verified eng board |
| Best Buy | Phoenix presence | Low–medium eng signal | https://jobs.bestbuy.com | Unknown | — | No | defer | Unverified eng board access |
| Blue Yonder | Scottsdale | Medium–high — supply-chain SaaS | https://blueyonder.com/careers | Unknown / custom | — | No | defer | Plausible local SaaS; tenant unverified |
| Grand Canyon Education | Phoenix | Medium — edtech | https://www.gce.com/careers/ | Custom | — | No | defer | Eng volume unclear |
| Charles Schwab | Phoenix footprint | Medium–high — finance eng | https://www.aboutschwab.com/careers | Custom / enterprise | — | No | defer | Presence known; ATS not verified for registry |

## Classifications

### active
Verified registry entries in `config/discovery_boards.json` with a working provider (Greenhouse, Lever, Ashby, Workday CXS, or Oracle CE). Live Discovery fetches openings; metro hints are planning-only.

### researched-unsupported
Important local or high-value employers whose careers systems are unresolved Workday CXS, SSO, or custom. Documented for gap analysis; **not** added to the registry without a verified public feed.

### defer
Plausible employers without a verified public tenant, weak current AZ engineering footprint, or low eng signal relative to research cost.

## Sector coverage (research set)

| Sector | Example employers in matrix |
|--------|----------------------------|
| Semiconductors | Intel, NXP, Microchip, onsemi, TSMC Arizona, Amkor, First Solar |
| Finance / fintech | Amex, Wells Fargo, Capital One, PayPal, Early Warning, Affirm, Chime, SoFi, Block, Stripe, Ramp, DriveTime |
| Insurance | USAA, State Farm, GEICO |
| Aerospace / defense | Honeywell, Northrop Grumman, Boeing, RTX, Axon, Waymo, Zoox |
| Healthcare | Banner Health, Mayo Clinic, UnitedHealthcare / Optum |
| Retail / consumer | Target, Carvana, PetSmart, Best Buy, Choice Hotels |
| Utilities | SRP, APS |
| Telecom | Cox / Cox Automotive, Lumen, GoDaddy |
| Logistics / waste / mining | Freeport-McMoRan, Republic Services, Amazon AZ ops, Avnet |
| SaaS / developer tools | Virtuous, GoHighLevel, Notion, Linear, Discord, GitLab, HashiCorp, Cursor, Supabase, Blue Yonder |

## Gap report

```
Employers researched: 74

Registry active:
  Greenhouse: 18
  Lever: 4
  Ashby: 11
  Workday (verified CXS): 9
  Oracle (verified CE): 2
  Subtotal: 44

researched-unsupported: 24
  Workday HTML / CXS unresolved: 4 (Freeport-McMoRan, Banner Health, RTX, Wells Fargo)
  Workday-shaped / fragile (no triple): 3 (Microchip, onsemi, TSMC Arizona)
  Custom / SSO / other: 17

defer: 6

Directly supported before Oracle: 42
Newly unlocked by Oracle: 2 (American Express, Honeywell)
Still blocked for Type-A Discovery: 30 (unsupported + defer)
Estimated direct coverage: 44/74 ≈ 59%
```

## Next ATS recommendation

**SmartRecruiters** is the single next provider after Oracle. Amex and Honeywell are unlocked via Oracle CE; remaining gaps are mostly custom/SSO or unresolved Workday site paths. SmartRecruiters offers the best remaining feasibility × data-quality path when public company postings are enabled. See [ats-coverage-analysis.md](./ats-coverage-analysis.md).

Do not register Freeport / Banner / RTX / Wells Fargo Workday boards until host/tenant/site triples return CXS 200.

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
