#!/usr/bin/env python3
"""
Inspect a site's robots.txt for Google + AI-retrieval bot allow status.

The cross-LLM retrieval layer is the part most SEO audits miss. We classify bots
into three categories per provider documentation:

  - retrieval bots (MUST be allowed for search-time visibility):
      Googlebot, Bingbot, OAI-SearchBot, ChatGPT-User, PerplexityBot,
      Perplexity-User, Claude-User, ClaudeBot (search), DuckDuckBot
  - training bots (business/IP decision; blocking them does NOT block retrieval):
      GPTBot, ClaudeBot, Google-Extended, CCBot, anthropic-ai, Bytespider, etc.
  - other crawlers: emit as informational

Usage:
    python robots_inspect.py https://example.com --out robots.json
"""

import argparse
import json
import sys
from typing import Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

try:
    import requests
except ImportError:
    print("Error: pip install requests", file=sys.stderr)
    sys.exit(1)


# Authoritative per-provider sources are cited in docs/rules.yaml entries
# (cross-llm-access category). Keep names case-sensitive as published.
RETRIEVAL_BOTS = [
    "Googlebot",
    "Bingbot",
    "OAI-SearchBot",
    "ChatGPT-User",
    "PerplexityBot",
    "Perplexity-User",
    "Claude-User",
    "Claude-SearchBot",
    "DuckDuckBot",
    "Applebot",
]

TRAINING_BOTS = [
    "GPTBot",
    "ClaudeBot",
    "anthropic-ai",
    "Google-Extended",
    "CCBot",
    "Applebot-Extended",
    "Bytespider",
    "Amazonbot",
    "Meta-ExternalAgent",
    "FacebookBot",
    "cohere-ai",
]


def fetch_robots(base: str) -> tuple[str, Optional[int]]:
    parsed = urlparse(base if "://" in base else f"https://{base}")
    url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "google-audit/0.1"})
        return (r.text if r.status_code == 200 else ""), r.status_code
    except requests.RequestException:
        return "", None


def parse_groups(raw: str) -> list[dict]:
    """Parse robots.txt into per-user-agent groups."""
    groups: list[dict] = []
    current: Optional[dict] = None
    for raw_line in raw.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if current is None or current.get("rules"):
                current = {"agents": [value], "rules": [], "crawl_delay": None}
                groups.append(current)
            else:
                current["agents"].append(value)
        elif key in ("disallow", "allow"):
            if current is None:
                current = {"agents": ["*"], "rules": [], "crawl_delay": None}
                groups.append(current)
            current["rules"].append({"directive": key, "path": value})
        elif key == "crawl-delay":
            if current is not None:
                try:
                    current["crawl_delay"] = float(value)
                except ValueError:
                    pass
    return groups


def evaluate_agent(groups: list[dict], agent: str) -> dict:
    """Evaluate whether `agent` is blocked at root '/' by robots.txt."""
    rp = RobotFileParser()
    rp.parse([line for g in groups for line in _group_to_lines(g)])
    can_root = rp.can_fetch(agent, "/")
    can_random = rp.can_fetch(agent, "/some-arbitrary-test-path-12345")
    matched_group = None
    blanket = False
    for g in groups:
        if any(a.lower() == agent.lower() for a in g["agents"]):
            matched_group = g
            for r in g["rules"]:
                if r["directive"] == "disallow" and r["path"] in ("/", ""):
                    if r["path"] == "/":
                        blanket = True
            break
    return {
        "agent": agent,
        "can_fetch_root": can_root,
        "can_fetch_arbitrary": can_random,
        "explicit_group": matched_group is not None,
        "blanket_disallow_root": blanket,
        "crawl_delay": (matched_group or {}).get("crawl_delay") if matched_group else None,
    }


def _group_to_lines(g: dict) -> list[str]:
    lines = []
    for a in g["agents"]:
        lines.append(f"User-agent: {a}")
    for r in g["rules"]:
        lines.append(f"{r['directive'].capitalize()}: {r['path']}")
    if g.get("crawl_delay") is not None:
        lines.append(f"Crawl-delay: {g['crawl_delay']}")
    return lines


def disallow_for_agents(groups: list[dict], agents: list[str]) -> list[str]:
    """Return the subset of `agents` whose root '/' is blocked."""
    blocked = []
    for a in agents:
        ev = evaluate_agent(groups, a)
        if not ev["can_fetch_root"]:
            blocked.append(a)
    return blocked


def inspect(url: str) -> dict:
    raw, status = fetch_robots(url)
    groups = parse_groups(raw)
    sitemaps = [line.split(":", 1)[1].strip() for line in raw.splitlines() if line.lower().startswith("sitemap:")]
    retrieval = {a: evaluate_agent(groups, a) for a in RETRIEVAL_BOTS}
    training = {a: evaluate_agent(groups, a) for a in TRAINING_BOTS}
    # Heuristic for CSS/JS blocking — any group disallowing common asset paths
    asset_paths = ["/static/", "/assets/", "/_next/", "/dist/", "/css/", "/js/", "/scripts/"]
    asset_blocks: list[str] = []
    for g in groups:
        for r in g["rules"]:
            if r["directive"] == "disallow" and any(r["path"].startswith(p) for p in asset_paths):
                asset_blocks.append(f"{', '.join(g['agents'])} -> Disallow: {r['path']}")
    # Crawl-delay flagged for Googlebot specifically (Google ignores it)
    googlebot_crawl_delay = retrieval["Googlebot"]["crawl_delay"]

    return {
        "status_code": status,
        "raw": raw,
        "size_bytes": len(raw.encode("utf-8")),
        "size_limit_bytes": 500 * 1024,
        "encoding_utf8": _looks_utf8(raw),
        "groups": groups,
        "sitemaps_declared": sitemaps,
        "retrieval_bots": retrieval,
        "training_bots": training,
        "disallow_for_retrieval_bots": disallow_for_agents(groups, RETRIEVAL_BOTS),
        "asset_blocks": asset_blocks,
        "googlebot_crawl_delay_present": googlebot_crawl_delay is not None,
    }


def _looks_utf8(raw: str) -> bool:
    try:
        raw.encode("utf-8")
        return True
    except UnicodeEncodeError:
        return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("--out")
    args = p.parse_args()
    result = inspect(args.url)
    out = json.dumps(result, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out)
    else:
        print(out)


if __name__ == "__main__":
    main()
