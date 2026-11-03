# Ranked source list (Tatvic problem discovery)

> **Permissions:** [`MASTER_SPECIFICATION.md`](../../MASTER_SPECIFICATION.md) §5 and `SOURCES.md`.  
> This ranking does not by itself enable collection. X and YouTube stay behind `ENABLE_X` / `ENABLE_YOUTUBE`. Measured yield from `source_yield` / weekly `feed_statuses` should replace judgment rows as runs accumulate.

**Criteria weights:** in-scope precision 30%, independent voices 20%, evidence quality 15%, capability signal 15%, access and cost 10%, terms safety 10%.

| Rank | Source | Role | Score | Default wave? | Main risk |
| ----- | ----- | ----- | ----- | ----- | ----- |
| 1 | Official documentation and release notes | Capability, deprecations | 4.20 | Yes | Page layout changes |
| 2 | Stack Exchange | Demand | 4.05 | Yes | Many how-to questions |
| 3 | Reddit | Demand | 3.50 | Yes (pipeline; unused by weekly leads) | Noisy; actor limits |
| 4 | Hacker News | Capability corroboration | 3.25 | Yes | Skews engineering |
| 5 | Allowlisted India / market RSS | market, idea, company_trigger | measured per run | Yes (weekly articles) | Dead RSS, redirects |
| 6 | YouTube (Data API v3, 11-channel pilot) | Capability/context | 2.90 | Opt-in `ENABLE_YOUTUBE` | Quota; title/description density |
| 7 | X | Market / idea | — | Opt-in `ENABLE_X`; limit 50 | Noise; not KEEP_DEMAND by default |

Articles/blogs (allowlisted RSS) follow exact URL roles in SOURCES.md §5. Entrackr and Financial Express feeds are **not** live RSS; homepage Playwright fallback only.

## Section starting points (validate with section leads)

- **Analytics:** Stack Overflow GA4/GTM tags; Google Analytics subreddits; GA/GTM release notes.  
- **Cloud and Data Engineering:** BigQuery tags; Data Engineering SE; BigQuery, Dataform, Looker, Gemini Enterprise release notes.  
- **Data Science and AI/ML:** Cross Validated; Data Science SE; Vertex AI and BigQuery ML release notes.  
- **Agentic AI:** NL-to-SQL / analytics-agent discussions; Agent Platform / Gemini Enterprise notes.  
- **CRO:** Experiment statistics on Cross Validated; ecommerce/PPC communities; experimentation tool release notes.
- **Lane 1 commercial leads:** Medianama, Analytics India Magazine, ET/Mint/YourStory/Inc42, opt-in X/YouTube for Indian startup and D2C budget holders.

Official GCP consolidated feed: `https://docs.cloud.google.com/feeds/gcp-release-notes.xml`.

## YouTube channel configuration

Primary path is **YouTube Data API v3**. Apify is fallback when `YOUTUBE_API_KEY` is empty. The 11-channel pilot roster is listed in `SOURCES.md` §7 and `.env.example` (`YOUTUBE_CHANNEL_URLS`). Empty list = not configured.

## Spend knobs (can cost money)

LiteLLM/Gemini analysis, Firecrawl, Apify (Reddit/X/YouTube fallback), YouTube Data API quota. Weekly leads with Firecrawl unset uses Playwright + RSS; **LLM calls still cost** if the endpoint is billed. Bounded first run: `LEADS_MAX_LLM_CALLS=0` or a small integer. `python scripts/weekly_run.py` does not send email.
