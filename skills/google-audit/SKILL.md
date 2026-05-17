---
name: google-audit
description: SEO + GEO + AEO audit for any website. Maps every check to Google's published best practices for Search, AI Overviews, AI Mode, Core Web Vitals (LCP ≤ 2.5 s, INP ≤ 200 ms, CLS ≤ 0.1), structured data, and the cross-LLM retrieval layer (ChatGPT Search via OAI-SearchBot, Perplexity, Microsoft Copilot, Claude web search). Every finding cites the Google or provider doc it traces to. Verdict is Not Ready / Competitive / Leading. Use when the user asks to "audit website", "google audit", "SEO audit", "GEO audit", "AEO audit", "google best practices", "is my site AI-search ready", "AI Overviews readiness", "core web vitals check", "crawlability audit", "robots.txt review", "schema deprecation check", "are my AI bots allowed", "are LLM crawlers allowed", or provides a domain or URL to evaluate. Do NOT use for keyword research, backlink analysis, content writing, competitor analysis, or ranking strategy — this skill audits compliance with published Google guidance only.
user-invokable: true
argument-hint: <domain-or-url> [--max-pages N]
license: MIT
metadata:
  version: 0.1.0
  category: seo
  keywords:
    - seo
    - geo
    - aeo
    - ai-seo
    - ai-search
    - ai-overviews
    - core-web-vitals
    - google-search
    - lighthouse
    - structured-data
    - chatgpt-search
    - perplexity
    - microsoft-copilot
    - audit
---

# google-audit

A read-only audit that maps a website's current state to the rules in [`docs/rules.yaml`](../../docs/rules.yaml). Every rule cites a Google or provider doc URL; thresholds match Google's published numbers verbatim. The skill does NOT make recommendations beyond what the cited doc supports.

## When to use

Trigger when the user asks any of:
- "audit my site", "audit example.com", "google audit", "google best practices check"
- "is my site AI-search ready", "AI Overviews readiness", "GEO audit"
- "core web vitals", "lcp/inp/cls check"
- "schema deprecation check" (FAQPage, HowTo, sitelinks search box)
- "are my AI bots allowed" (OAI-SearchBot, PerplexityBot, etc.)
- "crawlability", "robots.txt review"

Do NOT use for: keyword research, backlink analysis, content writing, competitor analysis, ranking strategy, paid ads.

## How it runs

**Preferred — one-shot orchestration:**

```bash
SKILL_DIR="${CLAUDE_SKILL_DIR:-$(dirname "$(readlink -f "${BASH_SOURCE[0]:-$0}")")}"
pip install -q -r "$SKILL_DIR/requirements.txt"
bash "$SKILL_DIR/scripts/run_audit.sh" "$URL" --max-pages "${MAX_PAGES:-50}"
```

`run_audit.sh` runs all six steps below and writes `audit.json` + `audit.md`
to `/tmp/google-audit-<timestamp>/`. Pass `--out DIR` to override the
location, `--no-lighthouse` to skip the Lighthouse CLI step, `--no-psi`
to skip PageSpeed Insights.

The six-step manual breakdown below is for when you need to invoke a
single step (e.g. only re-render after editing the JSON, or only re-run
schema validation after a deploy).

### 0. Check prerequisites

```bash
python "$SKILL_DIR/scripts/google_auth.py" --check
```

If `PAGESPEED_API_KEY` is missing, warn the user but continue — Lighthouse lab data still works locally; only CrUX field data is unavailable.

### 1. Crawl

```bash
OUT=/tmp/google-audit-$(date +%s)
mkdir -p "$OUT"
python "$SKILL_DIR/scripts/crawl_site.py" "$URL" --max-pages "${MAX_PAGES:-50}" --out "$OUT"
```

This writes `$OUT/crawl.json` with the list of pages. Sitemap-first; falls back to BFS. Respects `robots.txt`.

### 2. Site-level inspection

```bash
python "$SKILL_DIR/scripts/robots_inspect.py" "$URL" --out "$OUT/robots.json"
curl -s -L --max-time 10 "$URL/llms.txt" -o "$OUT/llms_txt.txt" 2>/dev/null || true
```

### 3. Per-page facts (parallel-friendly)

For each URL in `$OUT/crawl.json`, create `$OUT/pages/<NNN>-<slug>/` and run:

```bash
# Fetch HTML
python "$SKILL_DIR/scripts/fetch_page.py" "$PAGE_URL" --output "$PAGE_DIR/page.html"

# Parse SEO elements
python "$SKILL_DIR/scripts/parse_html.py" "$PAGE_DIR/page.html" --url "$PAGE_URL" --json > "$PAGE_DIR/parsed.json"

# Inject the page URL into parsed.json (needed for hreflang reciprocity check)
python -c "
import json
p = json.load(open('$PAGE_DIR/parsed.json'))
p['_page_url'] = '$PAGE_URL'
with open('$PAGE_DIR/page.html') as f:
    html = f.read()
p['_viewport_present'] = ('name=\"viewport\"' in html) or (\"name='viewport'\" in html)
json.dump(p, open('$PAGE_DIR/parsed.json','w'), indent=2)
"

# Schema validation (deprecated types, self-serving reviews, expired entities)
python "$SKILL_DIR/scripts/schema_validate.py" "$PAGE_DIR/page.html" --out "$PAGE_DIR/schema.json"

# PageSpeed Insights (lab + CrUX field data) — skip if no API key
[ -n "$PAGESPEED_API_KEY" ] && python "$SKILL_DIR/scripts/pagespeed_check.py" "$PAGE_URL" --strategy mobile --json > "$PAGE_DIR/psi.json"

# Lighthouse CLI — optional, slower but exhaustive
which lighthouse >/dev/null && python "$SKILL_DIR/scripts/lighthouse_run.py" "$PAGE_URL" --out "$PAGE_DIR/lighthouse.json"
```

You can run pages in parallel (4 concurrent works well). Cap to `--max-pages 10` for quick smoke audits.

### 4. Gather facts

```bash
python "$SKILL_DIR/scripts/gather_facts.py" "$OUT" --out "$OUT/facts.json"
```

Merges crawl + robots + per-page subreports into the fact-path schema used by `docs/rules.yaml`.

### 5. Evaluate rules

```bash
python "$SKILL_DIR/scripts/rules_engine.py" --facts "$OUT/facts.json" --rules "$SKILL_DIR/docs/rules.yaml" --out "$OUT/audit.json"
```

### 6. Render Markdown

```bash
python "$SKILL_DIR/scripts/render_report.py" "$OUT/audit.json" --out "$OUT/audit.md"
```

Show the user the verdict line and a summary, then the path to both artifacts.

## Output

- **`audit.json`** — every rule × every page where applicable, with `status` (`pass` / `fail` / `needs_improvement` / `nice_not_done` / `skipped`), observed value, threshold, and `source_url` citing the Google doc.
- **`audit.md`** — human-readable: verdict header, severity summary table, failing-MUST callout, then findings grouped by category.

## Scoring rubric (Google-pure)

| Result | Verdict |
|---|---|
| Any **MUST** failing | **Not Ready** — fix before anything else |
| All MUST pass, ≥ 80 % SHOULD pass | **Competitive** — eligible across Google + other AI engines |
| All MUST + all SHOULD pass | **Leading** — content quality + entity authority + monitoring |

NICE rules don't move the verdict; they're tracked separately.

## Conflict resolutions baked in

The skill never emits outdated guidance. `docs/rules.yaml` reflects the May 2026 state of Google's docs:

- `rel=next/prev` — not recommended; NICE only ("harmless for Bing/a11y")
- FAQPage rich result — only for gov/health domains; else flagged as deprecated (sunset May 7, 2026)
- HowTo rich result — flagged as deprecated whenever present (retired 2024)
- Sitelinks search box (`potentialAction.SearchAction`) — flagged as deprecated (Nov 21, 2024)
- Dynamic rendering — flagged as deprecated for new builds
- Mobile-Friendly Test — never referenced (retired Dec 1, 2023); Lighthouse + PSI only
- FID — replaced by INP March 12, 2024; skill measures INP only
- `Crawl-delay` for Googlebot — flagged as ignored by Google
- `Google-Extended` as "AI Overviews opt-out" — explicitly NOT; only `noindex` / `nosnippet` control AI Overviews
- `llms.txt` — checked for presence only; reported informational, never as a ranking factor

## Example invocations

```
/google-audit example.com
/google-audit https://example.com --max-pages 25
/google-audit blog.example.com --max-pages 10
```

## What to tell the user when reporting

1. **Lead with the verdict** — "Not Ready / Competitive / Leading" from `audit.md`.
2. **List failing MUSTs** verbatim (status + title + page URL + fix hint + source URL).
3. **Group SHOULD failures** by category so the user can plan a sprint.
4. **Cite the Google doc** for every recommendation — never paraphrase without the link.
5. **Don't invent rules.** If the user asks about something not in `rules.yaml`, say so explicitly and offer to open a PR adding a rule with a Google-doc citation.

## Limits and honest disclosure

- **Field data (CrUX) requires real traffic.** Low-traffic and newer sites get `null` CrUX values for LCP/INP/CLS — Google needs enough real-user data before they publish field metrics. This is **expected and not a failure**: the rules engine marks these rules as `skipped`, not `fail`. Install Lighthouse CLI (`npm install -g lighthouse`) for synthetic lab-data fallback when CrUX is unavailable.
- **AI-citation monitoring is out of scope.** This skill audits compliance, not whether AI engines actually cite the site — that needs third-party tools.
- **The cross-LLM bot list evolves.** OpenAI/Anthropic/Perplexity publish new agents and rename existing ones; treat the user-agent strings in `robots_inspect.py` as a snapshot, not a promise.
- **No magic AI-only files.** Google has explicitly stated no AI-specific markup is required. `llms.txt` is reported for presence only.
