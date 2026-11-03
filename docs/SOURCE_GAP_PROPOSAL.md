# Source-gap proposal (manual review only)

**Status:** Proposal — **not** authorization to crawl or edit `docs/tatvic/SOURCES.md`.  
**Date:** 2026-10-29  
**Authority:** User must approve and edit `SOURCES.md` before any new domain/feed is ingested.

**Expanded catalogue:** See [`SOURCE_EXPANSION_CATALOG.md`](SOURCE_EXPANSION_CATALOG.md) for source-by-source candidates, verified feeds, evidence-role matrix, staged roadmap and pilot plan. This file remains the short category-gap brief.

## Why this exists

The approved default wave (official docs, Stack Exchange, Reddit, Hacker News, allowlisted articles) is strong for **practitioner pain** and **capability corroboration**. It does **not** reliably produce:

- Named target companies
- Dated company business triggers (launch, funding, expansion, market entry)
- Verified India presence for a specific account
- Explicit procurement / budget / vendor-search buying evidence
- Verified public outreach routes

Phase 6 therefore reports most product-ready rows as **validated problem opportunities**, not **commercial leads**.

## Candidate categories (do not crawl yet)

| Evidence gap | Candidate category | Intended role | Expected value | Limitations | Cost |
| --- | --- | --- | --- | --- | --- |
| Product launches / expansion / market entry | Allowlisted India business & technology news RSS | Company trigger + India context | “Why now” + India markers | Noise; vendor PR; geo ambiguity | Low (RSS) |
| Hiring / stack investment | Allowlisted company career pages or job RSS | Hiring/context signal | Possible analytics/data hiring | **Not** purchase proof | Low–med |
| Funding / legal entity presence | Official filings / investor / company pages | Dated trigger + company/India evidence | Entity & geography | Sparse; legal language | Low |
| Company-stated business pain | Allowlisted interviews / case studies / business statements | Company-specific problem evidence | Named account + problem | Marketing bias | Low–med |
| Practitioner pain | Existing Stack Exchange + Reddit | Demand | Demand floor | Rarely names companies | Existing |
| Platform developments | Existing docs / articles / HN | Capability / trend | Corroboration only | Not demand | Existing |

## Implementation prerequisites (user-owned)

1. Add exact hosts/feeds to `docs/tatvic/SOURCES.md`.
2. Confirm fetch budget (default 60d / 3d overlap) still applies.
3. Confirm evidence category tagging (demand / capability / company_trigger / India / buying).
4. Only then authorize a collector change.

## Explicit non-goals

- Do not enable X or YouTube by default.
- Do not treat release notes as customer demand.
- Do not invent company leads from anonymous practitioner posts.
