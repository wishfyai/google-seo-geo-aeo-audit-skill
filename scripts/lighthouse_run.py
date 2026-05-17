#!/usr/bin/env python3
"""
Run Lighthouse against a URL via the locally-installed `lighthouse` CLI and emit
a slim JSON summary keyed for rules_engine.py.

Requires: npm install -g lighthouse (or npx lighthouse).

Usage:
    python lighthouse_run.py https://example.com --out lh.json
    python lighthouse_run.py https://example.com --form-factor desktop
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Optional


CATEGORIES = ["performance", "accessibility", "best-practices", "seo"]


def find_lighthouse() -> Optional[list[str]]:
    if shutil.which("lighthouse"):
        return ["lighthouse"]
    if shutil.which("npx"):
        return ["npx", "--yes", "lighthouse"]
    return None


def run_lighthouse(url: str, form_factor: str = "mobile", timeout: int = 180) -> dict:
    cmd = find_lighthouse()
    if not cmd:
        return {
            "ok": False,
            "error": "lighthouse CLI not found. Install with: npm install -g lighthouse",
        }

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        out_path = f.name
    try:
        full = cmd + [
            url,
            "--quiet",
            "--output=json",
            f"--output-path={out_path}",
            "--chrome-flags=--headless=new --no-sandbox --disable-gpu",
            f"--form-factor={form_factor}",
            "--throttling-method=simulate",
            "--only-categories=" + ",".join(CATEGORIES),
        ]
        proc = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0 and not os.path.exists(out_path):
            return {
                "ok": False,
                "error": f"lighthouse exited {proc.returncode}",
                "stderr": proc.stderr[-2000:],
            }
        with open(out_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"lighthouse timed out after {timeout}s"}
    except FileNotFoundError as e:
        return {"ok": False, "error": str(e)}
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass

    return _slim(raw)


def _slim(lh: dict) -> dict:
    cats = lh.get("categories", {}) or {}
    audits = lh.get("audits", {}) or {}

    def cat_score(key: str) -> Optional[float]:
        c = cats.get(key)
        return c.get("score") if c else None

    def audit_num(key: str) -> Optional[float]:
        a = audits.get(key)
        return a.get("numericValue") if a else None

    def audit_score(key: str) -> Optional[float]:
        a = audits.get(key)
        return a.get("score") if a else None

    failing = [
        {
            "id": k,
            "title": v.get("title"),
            "description": v.get("description"),
            "score": v.get("score"),
        }
        for k, v in audits.items()
        if isinstance(v, dict) and v.get("score") is not None and v.get("score") < 1
    ]

    return {
        "ok": True,
        "lighthouse_version": lh.get("lighthouseVersion"),
        "fetch_time": lh.get("fetchTime"),
        "user_agent": lh.get("userAgent"),
        "categories": {
            "performance": cat_score("performance"),
            "accessibility": cat_score("accessibility"),
            "best_practices": cat_score("best-practices"),
            "seo": cat_score("seo"),
        },
        "metrics": {
            "lcp_ms": audit_num("largest-contentful-paint"),
            "fcp_ms": audit_num("first-contentful-paint"),
            "cls": audit_num("cumulative-layout-shift"),
            "tbt_ms": audit_num("total-blocking-time"),
            "tti_ms": audit_num("interactive"),
            "si_ms": audit_num("speed-index"),
            "ttfb_ms": audit_num("server-response-time"),
        },
        "audits_pass": {k: True for k, v in audits.items() if isinstance(v, dict) and v.get("score") == 1},
        "audits_fail": failing,
        "render_blocking_resources": audit_num("render-blocking-resources"),
        "uses_responsive_images": audit_score("uses-responsive-images"),
        "uses_optimized_images": audit_score("uses-optimized-images"),
        "viewport": audit_score("viewport"),
        "document_title": audit_score("document-title"),
        "meta_description": audit_score("meta-description"),
        "http_status_code": audit_score("http-status-code"),
        "link_text": audit_score("link-text"),
        "crawlable_anchors": audit_score("crawlable-anchors"),
        "is_crawlable": audit_score("is-crawlable"),
        "robots_txt": audit_score("robots-txt"),
        "image_alt": audit_score("image-alt"),
        "hreflang": audit_score("hreflang"),
        "canonical": audit_score("canonical"),
        "structured_data": audit_score("structured-data"),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("--form-factor", choices=["mobile", "desktop"], default="mobile")
    p.add_argument("--timeout", type=int, default=180)
    p.add_argument("--out", help="Write slim JSON to this path (default: stdout)")
    args = p.parse_args()

    result = run_lighthouse(args.url, args.form_factor, args.timeout)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
    else:
        print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
