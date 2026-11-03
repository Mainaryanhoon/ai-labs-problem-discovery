# Source Expansion Catalogue — Commercial Opportunity Discovery

**Status:** Research / proposal only — **not** authorization to edit `docs/tatvic/SOURCES.md`, crawl new hosts, change weights, activate `v0.2-commercial`, enable X/YouTube, or send email.  
**Date:** 2026-10-29  
**Authority:** User must review this catalogue, then manually update `SOURCES.md` before any collector work.  
**Related:** Expands [`SOURCE_GAP_PROPOSAL.md`](SOURCE_GAP_PROPOSAL.md) (category gaps) into a source-by-source catalogue, evidence-role matrix, staged roadmap, and pilot plan. Does **not** replace that document.

---

## 0. How this document relates to the source-gap proposal

[`SOURCE_GAP_PROPOSAL.md`](SOURCE_GAP_PROPOSAL.md) named **categories** of missing evidence (company triggers, hiring, filings, company-stated pain). This catalogue:

1. Lists concrete publishers, feeds, APIs and portals under those categories (and more).
2. Records what was **HTTP-probed in this research pass** vs what remains **partially verified / unverified**.
3. Maps each candidate to an evidence role that cannot silently inflate the demand floor.
4. Stages implementation so Tatvic can approve a **broad but controlled** expansion.

**Success criterion (product):** a credible path from practitioner pain → named, India-relevant business opportunity with a defensible reason to act — **not** a longer URL list or higher technical scores.

---

## 1. Current architecture (reuse; do not rebuild)

| Capability | Location / note |
| --- | --- |
| Allowlisted collectors | `app/docs_ingestion.py`, `app/ingestion.py` (SE, HN), `app/social_ingestion.py` (Reddit/X), `app/article_ingestion.py`, opt-in YouTube |
| Source roles (demand floor) | `app/source_roles.py` — model `KEEP_DEMAND` cannot override role |
| Fetch-budget helpers | `app/docs_filter_helpers.py` (`fetch_not_before`, `should_fetch_new_item`, 60d / 3d) |
| Yield / shortfall | `app/services/source_yield.py` + discovery report |
| Commercial fields (inactive ranking) | `app/commercial_qualification.py`, `app/commercial_scoring.py` (`v0.2-commercial` informational) |
| Canonical policy | `docs/tatvic/SOURCES.md` (user-maintained) |

**Reuse path:** new Wave-1 sources should enter as additional allowlisted RSS/Atom hosts (articles path) or new tagged SE/Reddit roster entries — not a parallel ingestion product. Company-trigger and buying sources need **new role keys** in policy + `source_roles.py` before they can qualify India/buying fields.

---

## 2. Evidence-role system (prevent false conclusions)

Roles describe what stored evidence **may support**. They are **not** interchangeable with model KEEP/DROP codes.

| Role | May count toward demand floor? | Company-level lead? | India relevance? | Dated trigger? | Buying evidence? | Buyer route? |
| --- | --- | --- | --- | --- | --- | --- |
| `demand` | **Yes** (only this role) | Rarely (only if company named in item) | Only if India markers in item | Soft (problem recurrence) | No (pain ≠ budget) | No |
| `capability` | No | No | No | Yes (product change / deprecation) | No | No |
| `capability_corroboration` | No (HN stays here) | No | No | Soft | No | No |
| `company_trigger` | No | Supports named account + “why now” | If India/market stated | **Yes** (requires date + URL) | No by itself | Soft |
| `india_evidence` | No | Supports India enum | **Yes** (`confirmed` only with direct evidence) | Soft | No | Soft |
| `buying_evidence` | No | Supports commercial lead | Soft | Soft | **Yes** | Soft |
| `buyer_route` | No | Soft | Soft | Soft | Soft | **Yes** (public route only) |
| `context` | No | Soft | Soft | Soft | No | Soft |
| `unknown` | **No** (diagnostics only) | No | No | No | No | No |

### Rules

1. **Demand eligibility:** only sources whose configured role is `demand` contribute to demand item/author/platform floors. Model `KEEP_DEMAND` never overrides this (Phase 6).
2. **Company-level qualification:** requires a named company from approved evidence plus problem link; anonymous practitioner pain → **validated problem opportunity**, not a fabricated lead.
3. **India:** `confirmed` / `plausible_unverified` / `none` stay separate from score; famous global brand ≠ India proof.
4. **Dated trigger:** `company_trigger` (or capability deprecation) with verifiable date + source URL.
5. **Buying:** RFP/tender/explicit vendor search/budget language. Job posts = investment/context signal only. Funding = timing trigger only.
6. **Multi-role items:** one stored submission may carry secondary role tags in analysis/metadata, but it still counts as **one** independent demand item **only if** the source’s primary role is `demand`.
7. **Unknown sources:** do not count as demand; surface `mapping_known=false` in diagnostics.
8. **Duplicates / syndication:** canonicalize URL + native id; treat wire/PR syndication as one observation family across publishers when body/title hashes match; never multiply independence by republishing.
9. **Provenance:** every claim links to `Submission` / `SourceObservation` (URL, native id, quote substring, date).

### Policy / schema changes required (document only — not applied this phase)

| Change | Why |
| --- | --- |
| Extend `SOURCES.md` role column beyond demand/capability/context | Need `company_trigger`, `india_evidence`, `buying_evidence`, `buyer_route` |
| Extend `app/source_roles.py` `SourceRole` Literal + registry | Enforce new roles at scoring layer |
| Optional DB fields or analysis JSON for secondary evidence tags | Distinguish trigger vs buying vs India on one item |
| Article host allowlist expansion | Wave-1 India news RSS |
| **Do not** change v0.1 weights or activate v0.2 without Khilav/Ravi approval | Master + Phase 6 boundary |

**HN rule remains:** Hacker News = `capability_corroboration` only.

---

## 3. Fetch-budget compliance assessment (current code)

Canonical rule (RUBRIC §2.1 / SOURCES.md): cold start **60 days**; later runs from newest stored timestamp **− 3 days**; never re-fetch stored URL/native id.

| Collector | URL/native-id dedupe | 60d / 3d budget | Notes |
| --- | --- | --- | --- |
| Hacker News | Yes | **Yes** (`numericFilters` + `should_fetch_new_item`) | Compliant pattern to copy |
| Articles | Yes | **Yes** (`should_fetch_new_item`) | Dependent on working feed URLs |
| Official docs Atom | Yes | **Partial** — dedupe only; **no** `fetch_not_before` / date gate in `collect_official_docs` | Gap: may re-walk old feed entries until IDs known |
| Stack Exchange | Yes | **Partial** — sorts by creation but **no `fromdate`** tied to `fetch_not_before` | Gap: waste quota; older pages still fetched until limit |
| Reddit (Apify) | Yes | **Partial** — hygiene helpers exist; actor pull not fully cursor-budgeted in collector | Gap + paid cost risk |
| X / YouTube | N/A (opt-in) | Incomplete | Must stay opt-in |

**Roadmap implication:** before or with Wave 1, finish SE/docs/Reddit budget parity with HN (engineering estimate **0.5–1.5 days**). Do not expand paid collection to chase volume while budget gaps remain.

**Verified this phase:** code inspection only — no new live crawls of product pipelines.

---

## 4. Access verification method (this research pass)

- HTTP GET with browser-like User-Agent, follow redirects, timeout ~20s (2026-10-29).
- **Verified:** HTTP 200 + recognizable RSS/Atom/JSON API payload.
- **Partially verified:** official docs page or HTML portal reachable; structured feed/API not confirmed or blocked for automation.
- **Unverified:** claimed by secondary directories / docs but not successfully fetched here, or access failed (403/404/429/empty).

Probe artefacts (ephemeral): `data/tmp_research/feed_probe*.txt` (local research only; not a product path).

**Important finding:** currently approved article feed  
`https://cloud.google.com/blog/products/data-analytics/rss` returned **HTML** (200) rather than RSS in this probe — treat as **broken/redirected** until re-validated before relying on it in production.

---

## 5. Source catalogue

Priority: **P0** high-value + feasible; **P1** useful / more effort; **P2** exploratory; **Manual-only** human research, not automation yet.

### 5.A Practitioner demand and recurring operational pain

| Source name | Official URL | Geography | Tatvic domain | Evidence role | Evidence example | Access method | Access verification | Auth | Cost | Constraints | Freshness | Duplication risk | Reliability | Priority | Reason for inclusion | Known limitations |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Stack Overflow (existing tags) | https://api.stackexchange.com/docs/ ; tags per SOURCES.md | Global | Analytics, Cloud/DE, DS/AI | `demand` | GA4/GTM/BQ implementation failure quotes | Official API | **Verified** (API 200) | Optional app key | Free / quota | 30 rps concurrent; ~10k/day with key; backoff; pagesize≤100 | Yes with `fromdate` (not yet wired) | Cross-post to other SE sites | UGC Q&A | P0 (existing) | Core independent demand | Rarely names India companies |
| Stack Overflow — additional tags (proposed) | e.g. `google-tag-manager`, `server-side-tagging`, `looker`, `looker-studio`, `firebase-analytics` (confirm tag existence before add) | Global | Analytics, Cloud | `demand` | SST / Looker pain | API | **Partial** — `server-side*` tag search returned items; exact tag names need owner pick | Optional key | Free | Same SE throttle | Yes | Tag synonym noise | UGC | P0 | Broaden demand without new platforms | Out-of-scope DevOps tags must stay out |
| Web Applications SE — google-analytics | https://webapps.stackexchange.com/ ; Atom tag feed probed | Global | Analytics | `demand` | Non-dev GA config pain | SE API (`site=webapps`) + optional Atom | **Verified** (Atom 200 for tag feed) | Optional key | Free | Same SE | Yes | Overlap with SO | UGC | P1 | Captures practitioner ops questions SO misses | Lower volume; more “how do I click” |
| Cross Validated / Data Science / Data Engineering (existing) | SOURCES.md sites | Global | DS/AI, Cloud | `demand` | Experiment / forecasting / BQ questions | SE API | **Verified** (existing path) | Optional key | Free | Same | Yes | Cross-site duplicates | UGC | P0 (existing) | Already in policy | Academic noise on stats |
| Reddit — approved roster (existing) | Subreddits in SOURCES.md | Global | Mixed | `demand` | Recurring GA4/BQ complaints | Apify actor | **Partial** (public `.rss` for some subs 200; Apify live unverified this phase) | Apify token | Paid usage | 50 posts/sub; keyword gate on noisy subs; Reddit 429 | Soft | Cross-post / bots | UGC | P0 (existing) | Demand floor | Rarely company-named; Apify cost |
| Reddit — candidate expansions | e.g. r/CRO (429 this probe), r/AskMarketing (429), r/PPC (200 RSS) | Global | CRO, Analytics | `demand` | CRO experiment backlog pain | Apify (preferred) / RSS (fragile) | **Partial** — PPC RSS verified; others rate-limited | Apify | Paid | Keyword gates mandatory | Soft | High noise | UGC | P1 | CRO section coverage | Must keep keyword filter; respect robots/ToS |
| Hacker News (existing) | Algolia HN API | Global | Mixed | `capability_corroboration` | Market discussion of GA4/BQ agents | Algolia API | **Verified** | None | Free | Query set + 60d filter | Yes | Story vs comment mix | UGC + links | P0 (existing) | Corroboration only | **Must not** enter demand floor |
| Google Developer Forums — Cloud / Data Analytics | https://discuss.google.dev/c/google-cloud/14 ; …/data-analytics/86 | Global | Cloud, Analytics, AI | `demand` (if practitioner question) / else `context` | BigQuery cost / pipeline pain | Discourse RSS (`.rss`) | **Verified** (category RSS 200) | None for public RSS | Free | Cloudflare may 403 some automated clients; reported RSS bot blocks | Yes | Vendor staff posts | UGC + Google | P0 | High-signal GCP practitioner pain | Filter staff/announcement posts; login walls for some actions |
| Google Analytics Help Community | https://support.google.com/analytics/community | Global | Analytics | `demand` | Tracking discrepancy threads | Public HTML browse; **no documented public API/RSS found** | **Partial** (community pages exist) | Google account for some actions | Free | No stable bulk API found | Soft | Duplicate of SO questions | UGC | Manual-only / P2 | Rich GA pain | Automation not recommended until Google publishes a permitted feed |
| Measure Chat (#measure Slack) | https://www.measure.chat/ ; join.measure.chat | Global | Analytics, CRO | `demand` | Implementation war stories | Invite Slack; no public firehose | **Partial** (public site) | Membership | Free community | Not scrapable as product default; ToS | Soft | Private DMs forbidden | UGC | Manual-only | Expert community | Do not automate Slack scraping |
| GitHub Issues — relevant public repos | GitHub REST search/issues docs | Global | Analytics, Cloud, Agentic | `demand` *or* `context` | Bug/limitation in GA4/GTM/BQ client libs | Official REST API | **Verified** (search/issues and repo issues 200; some repo queries 422 if repo missing) | Optional token | Free / rate-limited | Search: 30 req/min auth; allowlist repos only | Yes | Issue templates / bots | UGC on OSS | P1 | Tooling pain near Tatvic stack | Many issues are vendor bugs not service opportunities; allowlist carefully |
| Shopify Community | community.shopify.com | Global | eCommerce analytics | `demand` | Store tracking / attribution issues | Public forum; RSS paths 404 in probe | **Unverified** for bulk | Account often | Free | No verified RSS this pass | Soft | Merchant noise | UGC | P2 | eCommerce vertical | Needs access review before any scrape |

### 5.B India-focused company and market triggers

| Source name | Official URL | Geography | Tatvic domain | Evidence role | Evidence example | Access method | Access verification | Auth | Cost | Constraints | Freshness | Duplication risk | Reliability | Priority | Reason for inclusion | Known limitations |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Economic Times — Tech RSS | https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms | India | All (filter) | `company_trigger` + `india_evidence` | Named India tech firm expansion / AI push | RSS | **Verified** | None | Free | TIL RSS terms (link-back); high volume | Yes | Wire syndication | Independent reporting + PR | P0 | India named accounts + dates | Funding ≠ buying Tatvic; keyword filter required |
| Economic Times — Startups path (probe) | https://economictimes.indiatimes.com/tech/startups/rssfeeds/13357300.cms | India | All | `company_trigger` + `india_evidence` | Startup launch / raise | RSS | **Verified** (200 XML; thin payload possible) | None | Free | Same TIL terms | Yes | High PR density | Mixed | P0 | Startup triggers | Validate item richness before SOURCES add |
| ET Startup — recent RSS | https://startup.economictimes.indiatimes.com/rss/recent | India | All | `company_trigger` + `india_evidence` | Startup ecosystem headlines | RSS | **Verified** | None | Free | Separate host `startup.economictimes…` | Yes | Overlap ET Tech | Mixed | P1 | Alternate startup feed | Confirm allowlist host |
| YourStory | https://yourstory.com/feed | India | All | `company_trigger` + `india_evidence` | Founder interview naming measurement pain | RSS | **Verified** | None | Free | Marketing tone common | Yes | Cross-post | Independent + promo | P0 | Company-named India stories | Interviews ≠ procurement |
| Inc42 | https://inc42.com/feed (also https://inc42.org/rss/latest-posts) | India | All | `company_trigger` + `india_evidence` | Funding / product launch | RSS | **Verified** | None | Free | Large feeds; paywall risk on full text | Yes | Overlap ET/YourStory | Independent + promo | P0 | India tech triggers | Prefer RSS summary; Firecrawl only if allowlisted |
| LiveMint — Technology | https://www.livemint.com/rss/technology | India | All | `company_trigger` + `india_evidence` | Enterprise tech initiatives | RSS | **Verified** | None | Free | Publisher terms | Yes | Syndication | Independent | P0 | Broadsheet India tech | Still not buying proof |
| LiveMint — Companies / AI / Industry | https://www.livemint.com/rss/companies ; `/rss/AI` ; `/rss/industry` | India | All | `company_trigger` + `india_evidence` | Company announcements | RSS | **Verified** | None | Free | Volume | Yes | High | Independent | P0 | Company identity + India | Keyword filter to Tatvic domains |
| Business Standard — Technology | https://www.business-standard.com/rss/technology-106.rss | India | All | `company_trigger` + `india_evidence` | IT services / digital initiatives | RSS | **Verified** | None | Free | Terms | Yes | Syndication | Independent | P1 | India corp tech | Noise |
| Business Standard — Companies | https://www.business-standard.com/rss/companies-101.rss | India | All | `company_trigger` + `india_evidence` | Named company news | RSS | **Verified** | None | Free | Volume | Yes | High | Independent | P1 | Named accounts | Same |
| Hindu BusinessLine — Info-tech / Companies | https://www.thehindubusinessline.com/info-tech/?service=rss ; `/companies/?service=rss` | India | All | `company_trigger` + `india_evidence` | India corp / IT | RSS | **Verified** | None | Free | Terms | Yes | Syndication | Independent | P1 | Diversify publishers | Same |
| Moneycontrol — latest / business / technology | https://www.moneycontrol.com/rss/latestnews.xml ; `business.xml` ; `technology.xml` | India | All | `company_trigger` + `india_evidence` | Markets + company headlines | RSS | **Verified** | None | Free | Markets noise | Yes | High | Mixed | P1 | India company names | Weak Tatvic fit without keywords |
| NDTV Profit feed | https://feeds.feedburner.com/ndtvprofit-latest | India | All | `company_trigger` + `india_evidence` | Business headlines | RSS | **Verified** | None | Free | Broad | Yes | Syndication | Mixed | P2 | Extra India coverage | Low precision |
| Entrackr | https://entrackr.com/ | India | All | `company_trigger` + `india_evidence` | Internet-economy funding | Public site; **no official RSS verified**; third-party aggregators exist | **Unverified** for official feed | Unknown | Unknown | Prefer official only | Soft | High overlap | Independent | P2 / Manual-only until official feed | Strong India internet economy | Do not adopt unofficial aggregator APIs without legal review |
| Official company newsrooms / IR | Company-specific `/press`, `/newsroom`, `/investors` | India / global | All | `company_trigger` + optional `india_evidence` | “Launched analytics platform in India” | Manual allowlist of hosts + optional RSS | **Unverified** per company | None | Free | Must be explicitly allowlisted | Yes | PR language | **Primary source** | Wave 2 / Manual-first | Highest-trust triggers | Scale requires curated target-account list |
| BSE corporate announcements | https://www.bseindia.com/ (announcements UI) | India | All | `company_trigger` + `india_evidence` | Listed-co disclosures | Public HTML; anti-bot / no simple RSS verified | **Partial** (site 200) | None | Free | Automation fragile | Yes | Filings boilerplate | Primary (exchange) | P2 / Manual-only | India entity + dated event | Not analytics-specific |
| NSE | https://www.nseindia.com/ | India | All | `company_trigger` + `india_evidence` | Exchange notices | Often **403** to simple clients | **Partial / blocked** | None | Free | Strict bot controls | Soft | Same | Primary | Manual-only | Listed India entities | Prefer human IR lookup |
| SEBI listings page | https://www.sebi.gov.in/… | India | Context | `context` | Regulatory notices | HTML | **Partial** | None | Free | Not commercial buying | Soft | N/A | Primary | Manual-only | Rarely Tatvic service trigger | DROP_REGULATORY risk |
| MCA / company registry | https://www.mca.gov.in | India | Identity | `india_evidence` | Legal entity presence | Portal | **Partial** | Often session | Free | Not for bulk scrape casually | Soft | N/A | Primary | Manual-only | Confirm India entity | Not problem evidence |

### 5.C Buying intent, procurement and budget signals

| Source name | Official URL | Geography | Tatvic domain | Evidence role | Evidence example | Access method | Access verification | Auth | Cost | Constraints | Freshness | Duplication risk | Reliability | Priority | Reason for inclusion | Known limitations |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| GeM BidPlus (public bids) | https://bidplus.gem.gov.in/all-bids | India | Cloud, Analytics, AI (if keyword match) | `buying_evidence` + `india_evidence` | Govt tender for analytics / data platform | Public HTML search | **Partial** (page 200; no official RSS/API found) | None for browse | Free browse | WAF; complex UI; 15‑min lag stated on site | Soft | Same bid on CPPP | Primary procurement | Wave 3 / P1 | Strongest public buying signal in India | Many bids irrelevant; keyword precision critical; do not scrape recklessly |
| CPPP eProcurement | https://eprocure.gov.in/cppp/ | India | Same | `buying_evidence` + `india_evidence` | Central/state tenders | Public portal | **Partial** (200) | None for browse | Free | Captcha on advanced search (third-party notes) | Soft | Overlap GeM | Primary | Wave 3 / P1 | Public procurement | Third-party scrapers ≠ official permission |
| Third-party tender APIs (TenderPulse, Apify actors, etc.) | Various commercial | India | Same | `buying_evidence` (if cites official bid URL) | Structured tender rows | Paid API / actor | **Unverified** as Tatvic-approved | API key / Apify | **Paid** | Must retain link to official GeM/CPPP page | Soft | Aggregator lag | Secondary | P2 | Convenience only | Prefer official primary URL as provenance; legal/ToS review |
| Company career pages / Greenhouse / Lever boards | Employer-hosted | India / global | Analytics, DE, CRO, AI | `context` (hiring) — **not** `buying_evidence` | “Hiring GA4 / measurement lead – India” | Public job HTML/JSON boards | **Unverified** per employer | None | Free | Allowlist employers; no LinkedIn scrape | Yes | Reposts | Primary employer | Wave 2 / P1 | Investment signal + buyer function hint | Hiring ≠ budget for Tatvic services |
| Naukri / Indeed / LinkedIn Jobs | naukri.com ; indeed.com ; linkedin.com/jobs | India | Same | `context` | Analytics roles in India | No public Naukri API; Indeed RSS failed this probe; LinkedIn restricted | **Unverified / unsuitable** for default automation | Often login | Paid scrapers exist | Anti-bot; ToS | Soft | Extreme | Aggregator | Manual-only / P2 | India hiring landscape | Do **not** treat scrapers as approved; LinkedIn personal data forbidden |
| PR Newswire (global) | https://www.prnewswire.com/rss/news-releases-list.rss | Global | Mixed | `company_trigger` / rare `buying_evidence` | Vendor-selection or digital transformation PR | RSS | **Verified** | None | Free | Heavy PR bias | Yes | Extreme syndication | Vendor marketing | P2 | Occasional explicit partner search | Mostly promo; not independent demand |
| Agency / tech partner directories | e.g. Google Marketing Platform Partners | Global / India filter | Analytics | `buyer_route` / `context` | Public partner profile / contact | Public directory; Partners API historically exists but auth/permission fragile | **Partial** (directory HTML 200) | API key/OAuth often | Free browse | Directory is reference-only per Google | Soft | N/A | Vendor directory | Wave 2 / Manual-only first | Outreach route research | Not proof a company will buy; do not harvest private emails |

### 5.D Company identification, India presence and buyer discovery

| Source name | Official URL | Geography | Role | Access | Verification | Priority | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Official company website (About / Contact / Locations) | Per company | India/global | `india_evidence`, `buyer_route` | Manual / allowlisted fetch | Per company | Wave 2 Manual-first | Prefer “Contact sales” / office India pages over guessed emails |
| Official leadership / team pages | Per company | — | `buyer_route` (function) | Manual | Per company | Manual-only | Name individuals only with public evidence; prefer **buyer function** |
| India news + filings (above) | — | India | `india_evidence` | RSS / portals | Mixed | P0–P2 | Cross-check company legal name vs brand |
| Google Partner / GMP directory | https://partners.google.com/marketingplatform/ ; enterprise marketing portal partner search | Global | `buyer_route` / competitive context | Manual | Partial | Manual-only | Useful for ecosystem mapping, not target inventing |
| LinkedIn company pages | linkedin.com/company/… | — | Soft identity | Browser | Restricted | **Out of default automation** | No private profile scrape; no email guessing |

### 5.E Product and platform capabilities

| Source name | Official URL | Role | Access verification | Priority | Notes |
| --- | --- | --- | --- | --- | --- |
| GCP consolidated release notes Atom (existing) | https://docs.cloud.google.com/feeds/gcp-release-notes.xml | `capability` | **Verified** | P0 existing | Keep product allowlist |
| BigQuery release notes (existing) | https://cloud.google.com/feeds/bigquery-release-notes.xml | `capability` | **Verified** | P0 existing | — |
| Looker release notes | https://cloud.google.com/feeds/looker-release-notes.xml | `capability` | **Verified** | P0 | Add to SOURCES product list |
| Vertex AI release notes | https://cloud.google.com/feeds/vertex-ai-release-notes.xml | `capability` | **Verified** | P0 | Agentic / ML |
| Dataform release notes | https://cloud.google.com/feeds/dataform-release-notes.xml | `capability` | **Verified** | P0 | Cloud/DE |
| Google Marketing Platform blog RSS | https://www.blog.google/products/marketingplatform/rss/ | `capability` / `context` | **Verified** | P0 | Measurement / ads platform changes |
| GA products blog RSS | https://blog.google/products/marketingplatform/analytics/rss/ | `capability` | **Verified** | P0 | GA/GTM narrative releases |
| GMP 360 blog RSS | https://blog.google/products/marketingplatform/360/rss/ | `capability` | **Verified** | P1 | Enterprise marketing stack |
| Ads & commerce blog RSS | https://blog.google/products/ads-commerce/rss/ | `context` | **Verified** | P1 | Filter tightly |
| Developers Google Blog Atom | https://developers.googleblog.com/feeds/posts/default | `capability` / `context` | **Verified** | P1 | Broader; keyword filter |
| Official Google Blog RSS | https://www.blog.google/rss/ | `context` | **Verified** | P2 | Too broad alone |
| Cloud data-analytics blog RSS (**current SOURCES default**) | https://cloud.google.com/blog/products/data-analytics/rss | `capability` | **Failed as RSS** (HTML 200) | Fix / replace | **Do not treat as healthy** until feed restored or alternate URL confirmed |
| GA “What’s new” announcements | https://support.google.com/analytics/announcements/10707884 | `capability` | **Partial** (HTML help page) | P1 Manual / scrape-with-care | Dated releases; no Atom verified |
| GTM release notes help page | https://support.google.com/tagmanager/answer/4620708 | `capability` | **Partial** (HTML) | P1 Manual | Same |
| VWO blog feed | https://www.vwo.com/blog/feed/ | `capability` / `context` | **Verified** (thin RSS) | P2 | CRO vendor marketing — not demand |
| Optimizely insights feed | https://www.optimizely.com/insights/feed/ | — | **404** this probe | — | Do not add until URL re-verified |
| Practitioner blogs (Simo Ahava, Analytics Mania, etc.) | Various | `context` | Feeds 404 / errors this probe | Manual-only | Useful reading; not demand; verify feed before allowlist |

### 5.F Marketing, analytics, measurement and CRO signals (cross-cutting)

Prefer sources already listed that map to Tatvic sections (Analytics, CRO, Cloud/DE, DS/AI, Agentic). Explicitly **exclude** generic consumer complaints, generic SWE career angst, unrelated AI hype, SEO/copywriting-only threads (RUBRIC out of scope).

**Complementarity pattern (do not invent links):**

1. Reddit/SE/discuss.google.dev → recurring **problem**.
2. India RSS / company IR → **named account + dated trigger + India**.
3. GeM/CPPP or explicit RFP language → **buying**.
4. Official career page / public contact → **buyer function / route**.
5. GCP/GMP release notes → **why a Tatvic offering is newly feasible**.

Only join these when evidence refers to the **same company/problem**; rank change alone is not a delta.

### 5.G Additional categories

| Category | Example | Role | Priority | Limitation |
| --- | --- | --- | --- | --- |
| Industry associations / public research | NASSCOM reports, Deloitte digital India (manual PDFs) | `context` | Manual-only | Not independent demand; easy to over-claim market size |
| Public case studies (vendor or brand) | Google customer stories, brand blogs | `company_trigger` / weak `buying_evidence` | Manual / P2 | Marketing bias; still useful for named problems |
| TechCrunch Startups RSS | https://techcrunch.com/category/startups/feed/ (**Verified**) | `company_trigger` (global) | P2 | Weak India-first; use only with India entity evidence elsewhere |
| BBC Business RSS | https://feeds.bbci.co.uk/news/business/rss.xml (**Verified**) | `context` | P2 | Global macro noise |

---

## 6. Ranked portfolio strategy (value ≠ volume)

### What to optimize for

| Criterion | Weight in selection (qualitative) |
| --- | --- |
| Recurring business pain (demand) | High |
| Named company identification | High |
| India relevance | High |
| Dated trigger | High |
| True buying/procurement | Medium–High (scarce) |
| Outreach route | Medium |
| Tatvic section/asset fit | High |
| Access reliability / cost | High |
| Duplicate/noise risk | Penalty |
| Provenance | Hard requirement |

### Recommended balanced portfolio

| Layer | Sources | Job |
| --- | --- | --- |
| **Demand spine** | SE (+ modest tag/site expands), Reddit (current + cautious CRO), discuss.google.dev Data Analytics / BigQuery topics | Independent practitioner pain |
| **Capability spine** | Existing GCP feeds + Looker/Vertex/Dataform + GMP/GA blogs; **fix broken cloud blog RSS** | What changed / what is buildable |
| **India company layer** | ET Tech/Startups, YourStory, Inc42, LiveMint companies/tech/AI, BS/BL selectively | Named accounts + India + dates |
| **Buying layer** | GeM/CPPP keyword monitoring (later) | Procurement proof |
| **Route layer** | Manual official contact/IR pages for shortlisted accounts | Verified outreach |
| **Corroboration** | HN (unchanged role) | Trend only |

**Do not** add X/YouTube to default wave. **Do not** equate feed count with commercial-lead count.

---

## 7. Implementation roadmap

Effort ranges are **engineering estimates**, not commitments.

### Wave 1 — High-value, low-complexity (feeds + SE tags + budget parity)

**Candidates:** ET Tech + Startups RSS; YourStory; Inc42; LiveMint technology/companies/AI; GMP + GA blog RSS; Looker/Vertex/Dataform Atom; discuss.google.dev category RSS (Cloud data analytics); modest SO tag additions; **repair** broken `cloud.google.com/blog/products/data-analytics/rss` entry.

**Roles:** `company_trigger` + `india_evidence` for India news; `capability` for Google feeds; `demand` for discuss.google.dev only after role policy + classifier rules.

**Benefit:** First path to **named India accounts** attached to existing problem clusters; richer capability timing.

**Method:** Extend article allowlist + optional dedicated “news” collector cloning article path; extend docs feed list; add Discourse RSS collector or articles path; wire SE `fromdate`.

**Effort:** ~**3–6 engineer-days** (incl. tests + role registry extension).  
**OpEx:** Free RSS; LiteLLM analysis cost rises with volume — mitigate with keyword prefilter.  
**Risks:** PR noise; false India claims; feed ToS; Cloudflare on Discourse; duplicate syndication.

**Tests / acceptance:**

- Role registry: India news never increments demand floor.
- Fetch budget: SE/docs/news use `fetch_not_before`.
- Dedupes across ET/Inc42 syndication.
- Report sections: company_trigger / india counts visible; commercial leads still empty unless company+problem linked.
- No SOURCES.md edit without user approval; no live crawl in this phase.

**Dependencies:** User edits SOURCES.md; schema/role policy update; article host allowlist.

### Wave 2 — Company and buyer intelligence

**Candidates:** Curated official newsroom/IR/career hosts for priority verticals (eCommerce, BFSI, Auto, Media/OTT); manual buyer-route playbook; optional partner directory research.

**Roles:** `company_trigger`, `india_evidence`, `buyer_route`, hiring=`context`.

**Benefit:** Convert problem opportunities → **commercial-lead candidates** with outreach routes.

**Method:** Allowlisted host list + Firecrawl/Playwright only after budget; human-in-loop for first accounts.

**Effort:** ~**5–10 days** + ongoing curator time.  
**OpEx:** Firecrawl usage if enabled.  
**Risks:** Over-collection; PII mistakes; stale contact pages.

**Acceptance:** `india_relevance` enum populated only from stored evidence; no guessed emails; commercial lead count can be **zero**.

### Wave 3 — Buying intent and procurement

**Candidates:** GeM BidPlus / CPPP keyword watch for analytics, BigQuery, measurement, experimentation, AI analytics (exact keywords TBD by owners).

**Roles:** `buying_evidence` + `india_evidence`.

**Benefit:** Distinguish “has problem” from “likely to pay.”

**Method:** Prefer official portals; store official bid URL as provenance; avoid unpaid third-party scrapers unless ToS-cleared.

**Effort:** ~**5–15 days** depending on automation hardness.  
**OpEx:** Possibly paid aggregator later.  
**Risks:** WAF, captcha, irrelevant tenders, legal.

**Acceptance:** Buying field set only with quote + official URL; hiring never auto-promotes to buying.

### Wave 4 — Experimental

GitHub allowlisted repos; carefully scoped Reddit expands; TechCrunch/PR Newswire; third-party tender APIs; Measure Chat manual sampling; vendor CRO blogs.

**Effort/cost/risk:** Higher uncertainty; separate approval each.

---

## 8. Source-access and cost-risk summary

| Class | Cost | Main risk | Mitigation |
| --- | --- | --- | --- |
| Official Google Atom/RSS | Free | Broken URLs (already observed) | Probe in CI; yield=failed |
| India publisher RSS | Free | Noise, syndication, ToS | Keyword gate; dedupe; link-back |
| Stack Exchange API | Free + key | Quota | `fromdate`; app key |
| Reddit via Apify | **Paid** | Cost, noise, 429 | Caps; keyword gate; no volume chase |
| Discourse RSS | Free | Cloudflare 403 | Detect failure in yield; backoff |
| GeM/CPPP | Free browse / paid aggregators | Legal, WAF | Manual first; official URL provenance |
| Job boards | Scrapers paid; LinkedIn restricted | ToS, PII | Employer career pages only |
| Firecrawl/Playwright | Paid / compute | Untrusted HTML | Existing untrusted-text rules |

---

## 9. Recommended additions to `docs/tatvic/SOURCES.md`

**Do not apply automatically.** Split for manual edit.

### 9.1 Proposed additions (HTTP-verified this pass — still need your policy approval)

**Capability feeds**

- https://cloud.google.com/feeds/looker-release-notes.xml  
- https://cloud.google.com/feeds/vertex-ai-release-notes.xml  
- https://cloud.google.com/feeds/dataform-release-notes.xml  
- https://www.blog.google/products/marketingplatform/rss/  
- https://blog.google/products/marketingplatform/analytics/rss/  

**India company-trigger / India-evidence RSS hosts**

- https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms  
- https://economictimes.indiatimes.com/tech/startups/rssfeeds/13357300.cms  
- https://yourstory.com/feed  
- https://inc42.com/feed  
- https://www.livemint.com/rss/technology  
- https://www.livemint.com/rss/companies  
- https://www.livemint.com/rss/AI  

**Demand (new platform family)**

- https://discuss.google.dev/c/google-cloud/data-analytics/86.rss  
- (optional) https://discuss.google.dev/c/google-cloud/14.rss with tighter filters  

**Fix / replace**

- Re-validate or replace `https://cloud.google.com/blog/products/data-analytics/rss` (returned HTML in probe).

### 9.2 Needs further verification before SOURCES.md edit

- Exact SO tags: `server-side-tagging`, Looker Studio tag names, Firebase Analytics — confirm on SE.  
- Business Standard / Hindu BusinessLine / Moneycontrol — verified feeds exist; decide keyword gates and host allowlist.  
- `startup.economictimes.indiatimes.com` host allowlisting.  
- Inc42.org vs inc42.com feed preference.  
- GeM/CPPP automation method and legal.  
- GitHub repo allowlist (specific repos).  
- Additional Reddit subs (CRO etc.) under Apify caps.  
- Entrackr official feed (none verified).  
- Optimizely / Simo Ahava / Analytics Mania feeds (failed this probe).  
- Shopify community RSS (404).

### 9.3 Explicit non-recommendations for default wave

- X, YouTube (remain opt-in).  
- LinkedIn / Naukri automated scraping.  
- Unofficial news aggregator APIs as primary provenance.  
- Treating funding, hiring, or vendor blogs as demand or buying proof.

---

## 10. Controlled pilot plan (first approved wave)

**Scope:** After you edit SOURCES.md for a **small Wave-1 subset** (suggest: 3 India RSS + 2 GMP/capability feeds + discuss.google.dev data-analytics RSS + SE `fromdate` fix). One offline/deterministic test cycle, then **one** bounded live collect (only when you authorize).

### Metrics to record (observe; do not invent baselines)

| Metric | Definition |
| --- | --- |
| Fetched / new / duplicate / irrelevant | Per source yield |
| Demand-role accepted | Items with role=`demand` passing gates |
| Independent authors / platforms | Existing independence rules |
| Named companies extracted | Only from evidence quotes/metadata |
| India `confirmed` vs `plausible_unverified` vs `none` | Qualification enum |
| Dated triggers (≤60d and ≤90d for commercial timing) | Count with URL |
| Buying/procurement signals | Count with official URL |
| Verified outreach routes | Count with public URL |
| Traceability failures | Claims without submission id |
| Cost / failure rate | Apify/Firecrawl/LLM $ and error % |
| **Qualified commercial leads** vs **validated problem opportunities** | Separate tallies |

### Proposed acceptance thresholds (targets, not observed results)

| Gate | Proposed threshold |
| --- | --- |
| Demand floor inflation by news/capability | **0** incidents |
| Syndication double-count independence | **0** after dedupe |
| Commercial leads fabricated without company evidence | **0** |
| India `confirmed` without direct evidence | **0** |
| Source yield visibility | Every configured source shows attempted/not_requested/succeeded/failed |
| Useful pilot outcome | ≥1 problem cluster that gains a **named India company trigger** with quote+URL — **or** honest report that none appeared |

Commercial-lead yield may remain **0**; that is an acceptable honest pilot result.

---

## 11. Questions requiring your decision

1. **Wave-1 shortlist:** Approve the “proposed additions” list as-is, or cut to a smaller set (which hosts)?  
2. **Role policy:** Confirm new roles (`company_trigger`, `india_evidence`, `buying_evidence`, `buyer_route`) for SOURCES.md + `source_roles.py`?  
3. **discuss.google.dev:** Treat as `demand` or `context` until classifier maturity?  
4. **India news keyword allowlist:** Which terms must match before analysis spend (GA4, GTM, BigQuery, attribution, experiment, CDP, etc.)?  
5. **Broken cloud data-analytics RSS:** Replace, drop, or investigate alternate official URL?  
6. **Procurement (Wave 3):** Is GeM/CPPP in-scope for Tatvic Labs services, or out of scope (private enterprise only)?  
7. **Target-account curation (Wave 2):** Will Tatvic supply a seed list of India brands/verticals?  
8. **Paid Apify expansion:** Cap monthly spend before any Reddit roster growth?  
9. **v0.2-commercial:** Remains inactive until Khilav Joshi + Ravi Pathak approve? (Recommended: yes.)  
10. **Legal/ToS:** Who signs off publisher RSS + GeM automation before implementation?

---

## 12. What was verified vs not (this phase)

| Done | Not done |
| --- | --- |
| Read master + tatvic policy docs + gap/proposal | Edited `SOURCES.md` |
| Inspected collectors, roles, fetch helpers | Implemented new collectors |
| HTTP-probed many candidate feeds/APIs | Live product ingestion / Apify / email |
| Wrote this catalogue + roadmap + pilot | Activated v0.2 or changed weights |
| Documented fetch-budget gaps | Closed those gaps in code |

**No new live pipeline crawl. No email. No secrets. No X/YouTube enablement.**

---

## 13. Suggested next action for you

1. Mark Wave-1 hosts you approve.  
2. Answer §11 decisions (especially roles + discuss.google.dev + GeM scope).  
3. Manually patch `docs/tatvic/SOURCES.md`.  
4. Authorize an implementation phase that: extends `source_roles.py`, finishes SE/docs fetch budget, adds allowlisted collectors, and runs the §10 pilot.
