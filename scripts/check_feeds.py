"""Verify candidate RSS feeds before adding them to SOURCES.md. Free, no keys."""
import sys
import httpx

CANDIDATES = [
    "https://yourstory.com/feed",
    "https://inc42.com/feed",
    "https://www.livemint.com/rss/companies",
    "https://economictimes.indiatimes.com/tech/startups/rssfeeds/13357300.cms",
    "https://www.medianama.com/feed/",
    "https://analyticsindiamag.com/feed/",
    "https://www.thehindubusinessline.com/companies/feeder/default.rss",
    "https://www.business-standard.com/rss/companies-101.rss",
    "https://www.moneycontrol.com/rss/business.xml",
]
ok = 0
for url in (sys.argv[1:] or CANDIDATES):
    try:
        r = httpx.get(url, timeout=20, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 leads-checker"})
        looks_xml = any(t in r.text[:600].lower() for t in ("<rss", "<feed", "<?xml"))
        print(f"{'OK ' if r.status_code == 200 and looks_xml else 'BAD'} {r.status_code} final={r.url} {url}")
        ok += r.status_code == 200 and looks_xml
    except Exception as exc:
        print(f"ERR {type(exc).__name__} {url}")
print(f"{ok} usable")
