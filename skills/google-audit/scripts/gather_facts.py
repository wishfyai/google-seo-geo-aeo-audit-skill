#!/usr/bin/env python3
"""
Merge the on-disk outputs of the audit pipeline into a single normalized
facts.json keyed by the fact-paths used in docs/rules.yaml.

Expected on-disk layout (produced by SKILL.md's orchestration):

    <out_dir>/
      crawl.json
      robots.json
      llms_txt.txt          (optional)
      pages/
        000-<slug>/
          fetch.json        # output of fetch_page.py
          parsed.json       # output of parse_html.py --json
          psi.json          # output of pagespeed_check.py --json (optional)
          lighthouse.json   # output of lighthouse_run.py (optional)
          schema.json       # output of schema_validate.py

Usage:
    python gather_facts.py <out_dir> --out facts.json
"""

import argparse
import json
import os
import sys
from urllib.parse import urlparse


def _read_json(path: str):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _bool(x) -> bool:
    return bool(x)


def _images_alt_coverage(images: list) -> float:
    if not images:
        return 1.0  # vacuously true
    with_alt = sum(1 for img in images if (img.get("alt") or "").strip() != "")
    return with_alt / len(images)


def _meta_robots_contains(meta_robots, token: str) -> bool:
    if not meta_robots:
        return False
    return token.lower() in meta_robots.lower()


def _schema_flags(schema_report: dict) -> dict:
    if not schema_report:
        return {
            "schema_has_organization": False,
            "schema_has_website": False,
            "schema_has_webpage": False,
            "schema_has_breadcrumb": False,
            "schema_has_deprecated_howto": False,
            "schema_has_deprecated_faqpage": False,
            "schema_has_sitelinks_searchaction": False,
            "schema_self_serving_reviews_count": 0,
            "schema_expired_events_count": 0,
            "schema_expired_jobs_count": 0,
        }
    deprecated_types = {d["type"] for d in schema_report.get("deprecated_types", [])}
    return {
        "schema_has_organization": _bool(schema_report.get("has_organization")),
        "schema_has_website": _bool(schema_report.get("has_website")),
        "schema_has_webpage": _bool(schema_report.get("has_webpage")),
        "schema_has_breadcrumb": _bool(schema_report.get("has_breadcrumb")),
        "schema_has_deprecated_howto": "HowTo" in deprecated_types,
        "schema_has_deprecated_faqpage": "FAQPage" in deprecated_types,
        "schema_has_sitelinks_searchaction": _bool(schema_report.get("sitelinks_searchbox_action")),
        "schema_self_serving_reviews_count": len(schema_report.get("self_serving_reviews", [])),
        "schema_expired_events_count": len(schema_report.get("expired_events", [])),
        "schema_expired_jobs_count": len(schema_report.get("expired_jobs", [])),
    }


def _site_facts(out_dir: str) -> dict:
    crawl = _read_json(os.path.join(out_dir, "crawl.json")) or {}
    robots = _read_json(os.path.join(out_dir, "robots.json")) or {}
    llms_txt = os.path.exists(os.path.join(out_dir, "llms_txt.txt"))

    base = crawl.get("base", "")
    scheme = urlparse(base).scheme if base else ""

    retrieval = robots.get("retrieval_bots", {}) or {}
    training = robots.get("training_bots", {}) or {}

    def allowed(name: str) -> bool:
        r = retrieval.get(name) or training.get(name)
        if r is None:
            return True
        return _bool(r.get("can_fetch_root"))

    # google_extended_misuse_signal — heuristic: if Google-Extended is Disallowed
    # AND no separate nosnippet/noindex strategy is in place at the page level,
    # the site may be relying on Google-Extended as an "AI Overviews opt-out"
    # — which it isn't. We flag presence of the Disallow as informational.
    google_extended_disallowed = not allowed("Google-Extended")

    # Entry status — derive from crawl manifest's first page, if available.
    entry_status = 200 if crawl.get("pages") else 0

    asset_blocks = robots.get("asset_blocks", []) or []
    sitemap_urls_found = len(crawl.get("pages", [])) if crawl.get("discovery") == "sitemap" else 0

    return {
        "site.entry_status": entry_status,
        "site.googlebot_allowed_root": allowed("Googlebot"),
        "site.bingbot_allowed_root": allowed("Bingbot"),
        "site.oai_searchbot_allowed_root": allowed("OAI-SearchBot"),
        "site.chatgpt_user_allowed_root": allowed("ChatGPT-User"),
        "site.perplexitybot_allowed_root": allowed("PerplexityBot"),
        "site.perplexity_user_allowed_root": allowed("Perplexity-User"),
        "site.robots_status": robots.get("status_code"),
        "site.robots_size_bytes": robots.get("size_bytes", 0),
        "site.robots_asset_blocks": asset_blocks,
        "site.googlebot_crawl_delay_present": _bool(robots.get("googlebot_crawl_delay_present")),
        "site.robots_sitemaps_declared": robots.get("sitemaps_declared", []) or [],
        "site.sitemap_urls_found": sitemap_urls_found,
        "site.scheme": scheme,
        "site.google_extended_misuse_signal": google_extended_disallowed,
        "site.llms_txt_present": llms_txt,
    }


def _page_facts(parsed: dict, schema_report: dict, psi: dict, lh: dict) -> dict:
    parsed = parsed or {}
    psi = psi or {}
    lh = lh or {}

    meta_robots = parsed.get("meta_robots") or ""
    canonical = parsed.get("canonical") or ""
    canonical_is_absolute = bool(canonical and (canonical.startswith("http://") or canonical.startswith("https://")))

    hreflang_entries = parsed.get("hreflang", []) or []
    page_url = parsed.get("_page_url")  # injected by SKILL.md
    self_referenced = True
    if hreflang_entries and page_url:
        self_referenced = any((h.get("href") or "").rstrip("/") == (page_url or "").rstrip("/") for h in hreflang_entries)

    viewport_present = False
    # parse_html doesn't surface viewport explicitly; SKILL.md may inject it.
    # Conservative: rely on raw HTML scan if available, else assume false.
    if parsed.get("_viewport_present") is not None:
        viewport_present = bool(parsed["_viewport_present"])

    # CrUX field data lives under different keys depending on pagespeed_check.py
    # output schema. We accept the most common shapes.
    crux = psi.get("loadingExperience") or psi.get("crux") or psi.get("field_data") or {}
    metrics = crux.get("metrics") or {}

    def crux_p75(key_options: list, divide_by: float = 1.0):
        for k in key_options:
            m = metrics.get(k)
            if isinstance(m, dict) and "percentile" in m:
                v = m["percentile"]
                return v / divide_by if isinstance(v, (int, float)) else None
        return None

    lcp_seconds = crux_p75(["LARGEST_CONTENTFUL_PAINT_MS"], divide_by=1000.0)
    inp_ms = crux_p75(["INTERACTION_TO_NEXT_PAINT", "EXPERIMENTAL_INTERACTION_TO_NEXT_PAINT"])
    cls = crux_p75(["CUMULATIVE_LAYOUT_SHIFT_SCORE"])
    if cls is not None and cls > 1:
        # CrUX returns CLS * 100 historically; normalize.
        cls = cls / 100.0
    ttfb_seconds = crux_p75(["EXPERIMENTAL_TIME_TO_FIRST_BYTE"], divide_by=1000.0)

    # Fall back to Lighthouse lab data if CrUX field data missing.
    if lcp_seconds is None and lh.get("metrics", {}).get("lcp_ms") is not None:
        lcp_seconds = lh["metrics"]["lcp_ms"] / 1000.0
    if cls is None and lh.get("metrics", {}).get("cls") is not None:
        cls = lh["metrics"]["cls"]
    if ttfb_seconds is None and lh.get("metrics", {}).get("ttfb_ms") is not None:
        ttfb_seconds = lh["metrics"]["ttfb_ms"] / 1000.0

    images = parsed.get("images", []) or []
    schema_flags = _schema_flags(schema_report)

    facts = {
        "page.url": page_url,
        "page.title": parsed.get("title") or "",
        "page.meta_description": parsed.get("meta_description") or "",
        "page.canonical_present_absolute": canonical_is_absolute,
        "page.meta_robots_contains_noindex": _meta_robots_contains(meta_robots, "noindex"),
        "page.meta_robots_contains_nosnippet": _meta_robots_contains(meta_robots, "nosnippet"),
        "page.h1_count": len(parsed.get("h1", []) or []),
        "page.word_count": parsed.get("word_count") or 0,
        "page.hreflang_self_referenced": self_referenced,
        "page.viewport_present": viewport_present,
        "page.images_alt_coverage": _images_alt_coverage(images),
        "crux.p75.lcp_seconds": lcp_seconds,
        "crux.p75.inp_ms": inp_ms,
        "crux.p75.cls": cls,
        "crux.p75.ttfb_seconds": ttfb_seconds,
    }
    facts.update({f"page.{k}": v for k, v in schema_flags.items()})
    return facts


def gather(out_dir: str) -> dict:
    pages_dir = os.path.join(out_dir, "pages")
    pages: list[dict] = []
    if os.path.isdir(pages_dir):
        for entry in sorted(os.listdir(pages_dir)):
            page_dir = os.path.join(pages_dir, entry)
            if not os.path.isdir(page_dir):
                continue
            parsed = _read_json(os.path.join(page_dir, "parsed.json"))
            schema_report = _read_json(os.path.join(page_dir, "schema.json"))
            psi = _read_json(os.path.join(page_dir, "psi.json"))
            lh = _read_json(os.path.join(page_dir, "lighthouse.json"))
            facts = _page_facts(parsed or {}, schema_report or {}, psi or {}, lh or {})
            pages.append({"dir": entry, "url": facts.get("page.url"), "facts": facts})

    return {
        "site": _site_facts(out_dir),
        "pages": pages,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("out_dir", help="Output directory used by the audit pipeline")
    p.add_argument("--out", default=None, help="Write merged facts.json here (default: <out_dir>/facts.json)")
    args = p.parse_args()
    facts = gather(args.out_dir)
    target = args.out or os.path.join(args.out_dir, "facts.json")
    with open(target, "w", encoding="utf-8") as f:
        json.dump(facts, f, indent=2)
    print(f"Wrote {target} — site facts + {len(facts['pages'])} pages", file=sys.stderr)


if __name__ == "__main__":
    main()
