#!/usr/bin/env python3
"""
Crawl a website: sitemap.xml first, then BFS from the entry URL, respecting robots.txt.

Usage:
    python crawl_site.py https://example.com --max-pages 50 --out /tmp/audit/
"""

import argparse
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from collections import deque
from typing import Optional
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Error: pip install requests beautifulsoup4 lxml", file=sys.stderr)
    sys.exit(1)


USER_AGENT = (
    "Mozilla/5.0 (compatible; google-audit/0.1; "
    "+https://github.com/google-audit)"
)
SITEMAP_NAMESPACES = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _normalize(url: str) -> str:
    url, _ = urldefrag(url)
    if url.endswith("/") and url.count("/") > 3:
        url = url.rstrip("/")
    return url


def _same_host(url: str, host: str) -> bool:
    try:
        return urlparse(url).hostname == host
    except Exception:
        return False


def fetch_robots(base: str) -> tuple[RobotFileParser, str, list[str]]:
    """Return parsed robots.txt, raw text, and discovered sitemap URLs."""
    parsed = urlparse(base)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = RobotFileParser()
    rp.set_url(robots_url)
    raw = ""
    sitemaps: list[str] = []
    try:
        r = requests.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=15)
        if r.status_code == 200:
            raw = r.text
            rp.parse(raw.splitlines())
            for line in raw.splitlines():
                if line.lower().startswith("sitemap:"):
                    sitemaps.append(line.split(":", 1)[1].strip())
        else:
            rp.parse([])
    except requests.RequestException:
        rp.parse([])
    return rp, raw, sitemaps


def fetch_sitemap_urls(sitemap_url: str, host: str, depth: int = 0) -> list[str]:
    """Fetch sitemap.xml (or index) and return the list of URLs for `host`. Recurses into sitemap indexes."""
    if depth > 3:
        return []
    urls: list[str] = []
    try:
        r = requests.get(sitemap_url, headers={"User-Agent": USER_AGENT}, timeout=20)
        if r.status_code != 200:
            return []
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError:
            return []
        # Sitemap index
        if root.tag.endswith("sitemapindex"):
            for sm in root.findall("sm:sitemap/sm:loc", SITEMAP_NAMESPACES):
                if sm.text:
                    urls.extend(fetch_sitemap_urls(sm.text.strip(), host, depth + 1))
        else:
            for loc in root.findall("sm:url/sm:loc", SITEMAP_NAMESPACES):
                if loc.text:
                    u = loc.text.strip()
                    if _same_host(u, host):
                        urls.append(_normalize(u))
    except requests.RequestException:
        pass
    return urls


def discover_sitemap(base: str, robots_sitemaps: list[str]) -> list[str]:
    parsed = urlparse(base)
    host = parsed.hostname or ""
    candidates = list(robots_sitemaps) or [
        f"{parsed.scheme}://{parsed.netloc}/sitemap.xml",
        f"{parsed.scheme}://{parsed.netloc}/sitemap_index.xml",
    ]
    discovered: list[str] = []
    for sm in candidates:
        discovered.extend(fetch_sitemap_urls(sm, host))
        if discovered:
            break
    # de-dupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for u in discovered:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def bfs_crawl(start: str, host: str, rp: RobotFileParser, max_pages: int, delay: float) -> list[str]:
    visited: set[str] = set()
    order: list[str] = []
    queue: deque[str] = deque([_normalize(start)])
    while queue and len(order) < max_pages:
        url = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        if not rp.can_fetch(USER_AGENT, url):
            continue
        try:
            r = requests.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.5"},
                timeout=20,
                allow_redirects=True,
            )
        except requests.RequestException:
            continue
        if r.status_code != 200 or "text/html" not in r.headers.get("content-type", "").lower():
            continue
        order.append(url)
        try:
            soup = BeautifulSoup(r.text, "lxml")
        except Exception:
            soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            full = _normalize(urljoin(url, href))
            if _same_host(full, host) and full not in visited:
                queue.append(full)
        time.sleep(delay)
    return order


def crawl(start: str, max_pages: int, out_dir: str, delay: float = 0.5) -> dict:
    parsed = urlparse(start if "://" in start else f"https://{start}")
    base = f"{parsed.scheme}://{parsed.netloc}"
    host = parsed.hostname or ""

    rp, robots_raw, robots_sitemaps = fetch_robots(base)
    sitemap_urls = discover_sitemap(base, robots_sitemaps)

    if sitemap_urls:
        source = "sitemap"
        urls = [u for u in sitemap_urls if rp.can_fetch(USER_AGENT, u)][:max_pages]
        if not urls:
            urls = sitemap_urls[:max_pages]
        # Ensure entry URL is included
        entry = _normalize(start if "://" in start else f"https://{start}")
        if entry not in urls and len(urls) < max_pages:
            urls.insert(0, entry)
    else:
        source = "bfs"
        urls = bfs_crawl(start if "://" in start else f"https://{start}", host, rp, max_pages, delay)

    os.makedirs(out_dir, exist_ok=True)
    manifest = {
        "entry": start,
        "base": base,
        "host": host,
        "discovery": source,
        "robots_txt_raw": robots_raw,
        "robots_sitemaps_declared": robots_sitemaps,
        "max_pages": max_pages,
        "pages": [{"url": u} for u in urls],
    }
    with open(os.path.join(out_dir, "crawl.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return manifest


def main():
    p = argparse.ArgumentParser(description="Crawl a site, sitemap-first then BFS, robots-aware.")
    p.add_argument("url", help="Entry URL or bare domain")
    p.add_argument("--max-pages", type=int, default=50)
    p.add_argument("--out", required=True, help="Output directory for crawl.json")
    p.add_argument("--delay", type=float, default=0.5, help="Per-request delay (s) for BFS")
    p.add_argument("--json", action="store_true", help="Echo the manifest to stdout")
    args = p.parse_args()

    manifest = crawl(args.url, args.max_pages, args.out, args.delay)
    if args.json:
        print(json.dumps(manifest, indent=2))
    else:
        print(f"Discovered {len(manifest['pages'])} URLs via {manifest['discovery']}", file=sys.stderr)
        print(f"Manifest: {os.path.join(args.out, 'crawl.json')}", file=sys.stderr)


if __name__ == "__main__":
    main()
