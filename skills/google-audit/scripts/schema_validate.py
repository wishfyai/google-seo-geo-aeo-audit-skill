#!/usr/bin/env python3
"""
Inspect JSON-LD structured data on a page: classify entities, surface deprecated
rich-result types, detect common spam patterns (self-serving reviews on
Organization/LocalBusiness, expired Events, etc.).

This is not a full schema.org validator — for vocabulary correctness use
validator.schema.org. We focus on the Google-specific checks that affect
real ranking + AI behavior in May 2026.

Usage:
    python schema_validate.py page.html --out schema.json
    cat page.html | python schema_validate.py --out schema.json
"""

import argparse
import datetime as dt
import json
import sys
from typing import Any

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("Error: pip install beautifulsoup4 lxml", file=sys.stderr)
    sys.exit(1)


# Types whose Google rich-result support has been retired or restricted.
# Citations live in docs/rules.yaml (entity-graph + ai-search-controls rules).
DEPRECATED_TYPES = {
    "HowTo": {
        "since": "2024",
        "note": "HowTo rich result fully retired in 2024. Markup is ignored by Google. Migrate steps to visible H2/H3 + VideoObject.",
        "source": "https://developers.google.com/search/blog/2023/08/howto-faq-changes",
    },
    "FAQPage": {
        "since": "2023-08",
        "note": "FAQPage rich result restricted to government/health domains (Aug 2023); broader support fully sunset May 7, 2026.",
        "source": "https://developers.google.com/search/blog/2023/08/howto-faq-changes",
    },
    "Course": {
        "since": "2025-06",
        "note": "Single-Course rich result retired June 2025. Course List carousel still supported.",
        "source": "https://developers.google.com/search/docs/appearance/structured-data/course-info",
    },
    "ClaimReview": {
        "since": "2025-06",
        "note": "ClaimReview rich result retired June 2025.",
        "source": "https://developers.google.com/search/docs/appearance/structured-data/factcheck",
    },
    "EstimatedSalary": {
        "since": "2025-06",
        "note": "Estimated Salary rich result retired June 2025.",
        "source": "https://developers.google.com/search/docs/appearance/structured-data/estimated-salary",
    },
    "LearningResource": {
        "since": "2025-06",
        "note": "Learning Video rich result retired June 2025.",
        "source": "https://developers.google.com/search/docs/appearance/structured-data/video",
    },
    "SpecialAnnouncement": {
        "since": "2025-06",
        "note": "Special Announcement rich result retired June 2025.",
        "source": "https://developers.google.com/search/docs/appearance/structured-data/special-announcements",
    },
    "VehicleListing": {
        "since": "2025-06",
        "note": "Vehicle Listing rich result retired June 2025.",
        "source": "https://developers.google.com/search/docs/appearance/structured-data/vehicle-listing",
    },
    "Quiz": {
        "since": "2026-01",
        "note": "Practice Problem (Quiz) rich result deprecated January 2026.",
        "source": "https://developers.google.com/search/docs/appearance/structured-data/practice-problem",
    },
}


def _walk(node: Any):
    """Yield every dict inside an arbitrarily nested JSON-LD structure."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _types_of(node: dict) -> list[str]:
    t = node.get("@type")
    if isinstance(t, list):
        return [str(x) for x in t]
    if isinstance(t, str):
        return [t]
    return []


def extract_jsonld(html: str) -> list[Any]:
    soup = BeautifulSoup(html, "lxml" if _has_lxml() else "html.parser")
    blocks: list[Any] = []
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            blocks.append(json.loads(script.string))
        except json.JSONDecodeError:
            blocks.append({"@parse_error": True, "raw_excerpt": script.string[:200]})
    return blocks


def _has_lxml() -> bool:
    try:
        import lxml  # noqa: F401
        return True
    except ImportError:
        return False


def analyze(html: str) -> dict:
    blocks = extract_jsonld(html)
    entities: list[dict] = []
    deprecated_hits: list[dict] = []
    self_serving_reviews: list[dict] = []
    expired_events: list[dict] = []
    expired_jobs: list[dict] = []
    sitelinks_searchbox: bool = False
    parse_errors = 0

    today = dt.date.today()

    for block in blocks:
        if isinstance(block, dict) and block.get("@parse_error"):
            parse_errors += 1
            continue
        for node in _walk(block):
            types = _types_of(node)
            if not types:
                continue
            entities.append({"types": types, "id": node.get("@id"), "name": node.get("name")})

            for t in types:
                if t in DEPRECATED_TYPES:
                    deprecated_hits.append({
                        "type": t,
                        "name": node.get("name"),
                        **DEPRECATED_TYPES[t],
                    })

            # Self-serving review / aggregateRating directly on Organization/LocalBusiness
            if any(t in {"Organization", "LocalBusiness"} or t.endswith("Business") for t in types):
                if "review" in node or "aggregateRating" in node:
                    self_serving_reviews.append({
                        "types": types,
                        "name": node.get("name"),
                        "has_review": "review" in node,
                        "has_aggregate_rating": "aggregateRating" in node,
                    })

            # Expired Event
            if "Event" in types:
                end = _parse_date(node.get("endDate") or node.get("startDate"))
                if end and end < today:
                    expired_events.append({"name": node.get("name"), "endDate": str(end)})

            # Expired JobPosting (validThrough in the past)
            if "JobPosting" in types:
                vt = _parse_date(node.get("validThrough"))
                if vt and vt < today:
                    expired_jobs.append({"title": node.get("title"), "validThrough": str(vt)})

            # Sitelinks search box pattern: WebSite + potentialAction.SearchAction
            if "WebSite" in types and isinstance(node.get("potentialAction"), (dict, list)):
                pa = node["potentialAction"]
                pa_list = pa if isinstance(pa, list) else [pa]
                for p in pa_list:
                    if isinstance(p, dict) and "SearchAction" in _types_of(p):
                        sitelinks_searchbox = True

    # Index entities present
    types_present = sorted({t for e in entities for t in e["types"]})

    return {
        "blocks_found": len(blocks),
        "parse_errors": parse_errors,
        "entities": entities,
        "types_present": types_present,
        "has_organization": any("Organization" in e["types"] for e in entities),
        "has_website": any("WebSite" in e["types"] for e in entities),
        "has_webpage": any("WebPage" in e["types"] for e in entities),
        "has_breadcrumb": any("BreadcrumbList" in e["types"] for e in entities),
        "has_article": any(t in e["types"] for e in entities for t in ("Article", "NewsArticle", "BlogPosting")),
        "deprecated_types": deprecated_hits,
        "self_serving_reviews": self_serving_reviews,
        "expired_events": expired_events,
        "expired_jobs": expired_jobs,
        "sitelinks_searchbox_action": sitelinks_searchbox,
    }


def _parse_date(value: Any):
    if not isinstance(value, str):
        return None
    # Accept ISO-ish formats; ignore time portion if present.
    s = value.split("T")[0]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("file", nargs="?", help="HTML file (default: stdin)")
    p.add_argument("--out")
    args = p.parse_args()

    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            html = f.read()
    else:
        html = sys.stdin.read()

    result = analyze(html)
    out = json.dumps(result, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out)
    else:
        print(out)


if __name__ == "__main__":
    main()
