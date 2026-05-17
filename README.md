# google-audit

[![skills.sh](https://skills.sh/b/wishfyai/google-seo-geo-aeo-audit-skill)](https://skills.sh/wishfyai/google-seo-geo-aeo-audit-skill)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Claude Code skill that audits any website against the best practices **Google itself publishes** for Search, AI Overviews, AI Mode, and Core Web Vitals — plus the cross-LLM retrieval layer that gates visibility in ChatGPT Search, Perplexity, Microsoft Copilot, and Claude web search.

Every finding cites the Google or provider doc it traces to. Thresholds are Google's verbatim numbers. The scoring rubric is **Not Ready / Competitive / Leading** — not a Lighthouse 0–100.

## Install

```bash
npx skills add https://github.com/wishfyai/google-seo-geo-aeo-audit-skill --skill google-audit
```

Then in Claude Code:

```
/google-audit example.com
/google-audit https://example.com --max-pages 25
```

### Or run it directly from the command line

After `npx skills add` (or after cloning the repo), there is a one-shot
orchestration script:

```bash
SKILL_DIR=./.agents/skills/google-audit   # path npx skills add lands the skill at
pip install -r "$SKILL_DIR/requirements.txt"
bash "$SKILL_DIR/scripts/run_audit.sh" https://wishfy.ai --max-pages 10
```

It writes `audit.json` + `audit.md` to `/tmp/google-audit-<timestamp>/`.
For richer field data, set `PAGESPEED_API_KEY` (free from Google) and/or
`npm install -g lighthouse`.

## What it does

1. **Crawls the site** — sitemap.xml first, falling back to BFS, respecting `robots.txt`, capped at `--max-pages` (default 50).
2. **Gathers facts per page** — rendered HTML + meta + structured data + Lighthouse (SEO / Accessibility / Best Practices / Performance) + PageSpeed Insights field data (CrUX p75 mobile).
3. **Site-level checks** — `robots.txt` correctness, AI-retrieval-bot allow-list (OAI-SearchBot, PerplexityBot, ChatGPT-User, Perplexity-User, Bingbot), sitemap discipline, HTTPS, hreflang.
4. **Evaluates ~80 rules** from [`docs/rules.yaml`](docs/rules.yaml) — every rule has a stable `id`, severity (`must`/`should`/`nice`), a Google doc citation, and a machine-evaluable check.
5. **Emits two artifacts**:
   - `audit.json` — every rule, every page, with status (`pass` / `needs_improvement` / `fail` / `nice_not_done`), observed value, threshold, source URL.
   - `audit.md` — human-readable summary with verdict at the top and clickable citation links.

## Scoring rubric

| Result | Verdict |
|---|---|
| Any **MUST** failing | **Not Ready** — fix before anything else. |
| All MUST pass, ≥ 80 % SHOULD pass | **Competitive** — eligible across Google + other AI engines. |
| All MUST + all SHOULD pass | **Leading** — focus on content quality, entity authority, monitoring. |

## Setup

### Required: PageSpeed Insights API key (free)

Field-data checks (Core Web Vitals) need a free Google API key:

1. Visit https://developers.google.com/speed/docs/insights/v5/get-started and click "Get a Key".
2. Export it:
   ```bash
   export PAGESPEED_API_KEY=AIza...
   ```

The skill warns and continues without it — Lighthouse lab data still runs locally — but CrUX field data is the recommended signal for ranking.

### Recommended: Lighthouse CLI

Used for full Lighthouse audits when PSI rate-limits or for offline checks:

```bash
npm install -g lighthouse
```

### Python deps

```bash
pip install -r requirements.txt
```

## Conflict resolutions baked in

The skill never emits outdated guidance. `rules.yaml` explicitly reflects the May 2026 state of Google's documentation:

| Topic | Behavior |
|---|---|
| `rel=next/prev` for pagination | Not recommended — fires only as NICE "harmless for Bing/a11y" |
| FAQPage rich result | Only for gov/health domains; else flagged as deprecated (sunset May 7, 2026) |
| HowTo rich result | Flagged as deprecated (fully retired 2024) |
| Sitelinks search box | `potentialAction.SearchAction` flagged as deprecated (Nov 21, 2024) |
| Dynamic rendering | Flagged as deprecated for new builds |
| Mobile-Friendly Test | Never referenced (retired Dec 1, 2023) |
| FID | Skill measures INP only (replaced FID March 12, 2024) |
| `Crawl-delay` for Googlebot | Flagged as ignored by Google (Bingbot honors it) |
| `Google-Extended` as "AI opt-out" | Explicitly NOT — does not affect AI Overviews or AI Mode |
| `llms.txt` | Checked for presence only; reported informational, never a ranking factor |

## Why another SEO skill?

If you already have `seo-audit`, `seo-technical`, or `seo-google` installed, this skill is **different**:

- **Pure Google adherence**: every rule cites a Google or provider doc URL. No third-party heuristics, no Lighthouse 0–100 obfuscation.
- **Cross-LLM bot-access layer**: the other SEO skills focus on Google. This one also gates visibility on ChatGPT Search, Perplexity, Copilot, and Claude web search — a layer most audits miss.
- **Severity that maps to action**: MUST = fix-before-launch, SHOULD = competitive, NICE = leading. The verdict at the top of `audit.md` tells you which.

## Project structure

```
google-audit/
├── .claude-plugin/plugin.json
├── skills/google-audit/SKILL.md   # entry point
├── scripts/                       # crawl, fetch, lighthouse, schema, rules engine, renderer
├── docs/
│   ├── rules.yaml                 # the checklist (source-of-truth)
│   └── google-sources/            # cached snapshots of cited docs
├── requirements.txt
├── LICENSE
└── README.md
```

## Contributing

Open a PR against `docs/rules.yaml` if you find a new Google-published threshold or a deprecation we missed. Every new rule must include `source.url` pointing to a Google or recognized provider doc.

## License

MIT
