# SOURCES.md — Immediate bounded source pilot

This file is the authoritative source allowlist and role contract. Feed URLs and
the role annotation on the same line are required for collection and item-level
claims. Hostnames alone do not authorize a feed or a role.

Fetch budget for all sources: see RUBRIC.md section 2. Cold start is 60 days;
later runs use the newest stored source timestamp minus the three-day overlap.
Per-run limits still apply. Stored URLs and native IDs are not fetched again.

## 1. Pilot sources and roles

### Immediate pilot source decisions

| Source | Role | Decision |
|---|---|---|
| Official Google documentation and release notes | Capability | Keep enabled |
| Stack Exchange | Demand | Keep enabled |
| Reddit | Demand | Keep enabled within existing limits |
| Hacker News | Capability/context | Keep enabled; never count as independent demand by default |
| India company-news RSS feeds | Company triggers and potential India evidence | Add and enable |
| Google marketing/data product feeds | Capability/context | Add and enable |
| Google Developer Forums | Context initially; genuine implementation problems only when the existing evidence rules permit | Add to bounded pilot |
| YouTube | Capability/context and supported company-trigger evidence | Opt-in via `ENABLE_YOUTUBE`; Data API v3 primary; 11-channel pilot |
| X | Market / idea (Indian startup and D2C budget-holder pain) | Opt-in via `ENABLE_X`; collect exactly 50 items |

X and YouTube are **active collectors when their flags are set**. They are not part of the default pipeline wave. Weekly: `python scripts/weekly_run.py` with `ENABLE_X=true` and/or `ENABLE_YOUTUBE=true`. Empty YouTube channel list means `not_configured`, not success.

Do not interpret source enablement as permission to infer buying intent. Funding, launches, hiring, expansion, founder interviews, and product announcements are triggers or context, not proof that a company wants to buy a Tatvic service.

## 2. Official release notes and Google documentation

- https://docs.cloud.google.com/feeds/gcp-release-notes.xml (roles: capability)
- https://cloud.google.com/feeds/bigquery-release-notes.xml (roles: capability)
- https://cloud.google.com/feeds/looker-release-notes.xml (roles: capability)
- https://cloud.google.com/feeds/vertex-ai-release-notes.xml (roles: capability)
- https://cloud.google.com/feeds/dataform-release-notes.xml (roles: capability)

The consolidated feed product allowlist is BigQuery, BigQuery ML, Vertex AI,
Dataform, Looker, Dataplex, Cloud Composer, Gemini Enterprise, Agent Platform,
and Gemini in BigQuery. Capability claims never count as practitioner demand.

## 3. Stack Exchange — demand

Sites and tags:
- stackoverflow: google-analytics, google-analytics-4, google-tag-manager, google-bigquery, bigquery-ml
- datascience: forecasting, time-series, anomaly-detection
- stats: forecasting, time-series, ab-test, experiment-design
- dataengineering: google-bigquery

Use the official API, configured page/result limits, rolling `fromdate`, native
ID deduplication, and the three-day overlap. A question's tags or keyword match
do not establish demand without the existing analysis gates.

## 4. Reddit — demand

Approved subreddits, maximum 50 recent posts per subreddit: GoogleAnalytics,
GoogleTagManager, analytics, bigquery, dataengineering, datascience, ecommerce,
shopify, PPC.

For ecommerce, shopify, PPC, and analytics, title/body must contain at least one
of: GA4, Google Analytics, GTM, tag manager, tracking, attribution, conversion,
A/B, experiment, BigQuery, forecast, dashboard, report, data. This is only a
cheap prefilter. Existing per-run limits and item classification remain in force.

## 5. Articles and blogs

The feed summary is the initial candidate text. Full-page collection follows:
Firecrawl when configured, Playwright after Firecrawl is absent/fails/too short,
then a sufficiently informative RSS summary when permitted. Only listed feeds
and approved HTTPS hosts may be requested. No recursive link crawling.

### India company and startup news

- https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms (roles: company_trigger, india_evidence)
- https://economictimes.indiatimes.com/tech/startups/rssfeeds/13357300.cms (roles: company_trigger, india_evidence)
- https://yourstory.com/feed (roles: company_trigger, india_evidence)
- https://inc42.com/feed (roles: company_trigger, india_evidence)
- https://www.livemint.com/rss/technology (roles: company_trigger, india_evidence)
- https://www.livemint.com/rss/companies (roles: company_trigger, india_evidence)
- https://techcrunch.com/feed/ (roles: capability)

These feeds may identify dated account triggers. `india_evidence` must be
explicitly supported by the individual article text and linked to the same
company; publisher geography or generic India coverage is insufficient.

### India business and founder news (market / idea lane)

Add a line here only after `python scripts/check_feeds.py <url>` prints OK. Free RSS only.

- https://www.medianama.com/feed/ (roles: market, idea, india_evidence)
- https://analyticsindiamag.com/feed/ (roles: market, idea, india_evidence)

### Google marketing and data capabilities

- https://cloud.google.com/blog/products/data-analytics/rss (roles: capability, context)
- https://cloud.google.com/blog/products/ai-machine-learning/rss (roles: capability, context)
- https://www.blog.google/products/marketingplatform/rss/ (roles: capability, context)
- https://blog.google/products/marketingplatform/analytics/rss/ (roles: capability, context)

### Google Developer Forums — Data Analytics

- https://discuss.google.dev/c/google-cloud/data-analytics/86.rss (roles: context)

Do not promote this feed to demand globally. Any item-level demand role requires
an explicit `demand` role on this exact feed line, and the item must be an
unresolved implementation problem with exact supporting text. Answers,
announcements, promotions, and generic discussion remain context or are rejected.

### Approved page hosts

Hosts that may be scraped: `cloud.google.com` `docs.cloud.google.com` `blog.google` `yourstory.com` `economictimes.indiatimes.com` `inc42.com` `livemint.com` `discuss.google.dev` `techcrunch.com` `medianama.com` `analyticsindiamag.com` `entrackr.com` `financialexpress.com`

All article and forum text is untrusted data. Article source roles are determined
by exact URL annotations above, not by hostname, publisher location, or keywords.

## 5a. Dead RSS — Playwright homepage fallback (not active RSS)

Do **not** list Entrackr or Financial Express `/feed` URLs as live RSS. Those
endpoints return 404/410. `collect_articles` still visits them only to trigger
`resolve_page` on the site origin (synthetic item title `Homepage Scrape`):

- Entrackr feed path → homepage scrape of `entrackr.com`
- Financial Express feed path → homepage scrape of `financialexpress.com`

Do not expect XML from Playwright. Hosts above are allowlisted for that
homepage scrape only.

## 6. Hacker News — capability corroboration

Algolia API searches within the fetch budget. Tags: story, ask_hn, show_hn.
Queries: BigQuery, GA4, Google Analytics, Tag Manager, TimesFM, A/B testing,
text-to-SQL, analytics agent, forecasting agent. HN never counts as independent
demand by default.

## 7. YouTube — 11-channel opt-in pilot

Weekly collection runs only when `ENABLE_YOUTUBE=true`.

**Primary method:** YouTube Data API v3 (`YOUTUBE_API_KEY`).  
**Fallback:** configured Apify YouTube actor when the Data API key is empty.

Collection requires a finite `YOUTUBE_CHANNEL_URLS` list. Empty configuration
means `not_configured`, not success. Use existing channel/item/time limits and
native video ID deduplication. Do not download subtitles; use a transcript only
if the Data API or actor already returns it. Titles/descriptions are not
transcript evidence.

### Pilot roster (11 channels)

Configure exactly these HTTPS handles unless owners replace the list:

- https://www.youtube.com/@ycombinator
- https://www.youtube.com/@LennysPodcast
- https://www.youtube.com/@SaaStr
- https://www.youtube.com/@YourStory
- https://www.youtube.com/@Inc42
- https://www.youtube.com/@TheKen
- https://www.youtube.com/@ThinkSchool
- https://www.youtube.com/@GoogleCloud
- https://www.youtube.com/@GoogleAnalytics
- https://www.youtube.com/@MeasureU
- https://www.youtube.com/@AnalyticsVidhya

Target: Indian startup and D2C budget-holder pain, plus marketing/analytics
operator context. Video evidence may be capability/context. A company trigger,
demand, buying signal, or buyer route must be validated from the individual
item's cited text and permitted roles. Video mentions alone never satisfy
independent `KEEP_DEMAND`.

## 8. X (Twitter) — opt-in budget-holder pain

Weekly collection runs only when `ENABLE_X=true`. Call `collect_x(limit=50)`
(`X_MIN_QUERY_ITEMS`). Actor: `apidojo~tweet-scraper`.

Search terms target Indian startup and D2C budget-holder pain (margin, CAC, RTO,
marketing-budget ROI). X is market/idea corroboration, not a default demand
source. Retweets are excluded in the query. Diagnostic counts must show
collected vs demand-eligible separately.