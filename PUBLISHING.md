# Publishing checklist

The repo at `/Users/apple/google-audit/` is ready to push. This checklist walks you through publishing to GitHub and making it installable via `npx skills add`.

## 1. Local sanity check

```bash
cd /Users/apple/google-audit
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Quick rules-engine smoke test (uses the same synthetic facts that verified
# the install)
.venv/bin/python scripts/rules_engine.py \
  --facts /tmp/google-audit-test/facts.json \
  --rules docs/rules.yaml \
  --out /tmp/audit.json
.venv/bin/python scripts/render_report.py /tmp/audit.json --out /tmp/audit.md
```

If both write successfully, the skill works.

## 2. Create the GitHub repo

```bash
cd /Users/apple/google-audit

# Initialize git
git init
git add .
git commit -m "Initial commit: google-audit skill v0.1.0

39 rules across 13 categories. Every finding cites a Google or provider
doc. Thresholds are Google's verbatim numbers. Verdict: Not Ready /
Competitive / Leading."

# Repo was created via the GitHub web UI under the wishfyai org as
# `google-seo-geo-aeo-audit-skill`. SSH push uses the wishfy key alias
# configured in ~/.ssh/config (Host github-wishfy).
```

If you ever need to recreate it: create the empty repo manually at
https://github.com/organizations/wishfyai/repositories/new (name:
`google-seo-geo-aeo-audit-skill`), do NOT initialize with README/license,
then:

```bash
git remote add origin git@github-wishfy:wishfyai/google-seo-geo-aeo-audit-skill.git
git branch -M main
GIT_SSH_COMMAND="ssh -i ~/.ssh/id_ed25519_wishfy -o IdentitiesOnly=yes" git push -u origin main
```

## 3. Verify install via `npx skills add`

From a different directory:

```bash
cd /tmp
npx skills add https://github.com/wishfyai/google-seo-geo-aeo-audit-skill --skill google-audit
```

The skill should appear in `~/.claude/skills/google-audit/` (or wherever your Claude Code install resolves skills to).

## 4. Verify the skill runs in Claude Code

In a new Claude Code session:

```
/google-audit example.com --max-pages 5
```

You should see a verdict (Not Ready / Competitive / Leading), a summary table, and pointers to `audit.json` + `audit.md`.

## 5. Optional: publish on skills.sh

skills.sh accepts skills via PR to their marketplace registry. Their submission process changes over time — check their current docs at the time of submission. The repo is structured to comply with the conventions: `.claude-plugin/plugin.json` manifest, `skills/<name>/SKILL.md` entry point, MIT license.

## 6. Optional: tag a release

```bash
git tag -a v0.1.0 -m "Initial release"
git push origin v0.1.0
gh release create v0.1.0 --title "v0.1.0 — Initial release" \
  --notes "39 rules across SEO + Core Web Vitals + AI Overviews + cross-LLM retrieval bots. Every finding cites a Google or provider doc."
```

## Notes on dependencies

The skill requires a Python environment with:
- `requests`, `beautifulsoup4`, `lxml`, `PyYAML` (in `requirements.txt`)

Optional, for the richest audit:
- `PAGESPEED_API_KEY` env var (free from https://developers.google.com/speed/docs/insights/v5/get-started) — enables CrUX field data
- `lighthouse` CLI (`npm install -g lighthouse`) — enables full Lighthouse audit

Without these, the skill still runs end-to-end on `fetch_page` + `parse_html` + `schema_validate` + `robots_inspect`, and the rules engine will mark CrUX-dependent rules as `skipped` rather than failing.

## Maintenance cadence

The plan and the SKILL.md both note that the cross-LLM bot list and Google's deprecation schedule evolve. Recommended cadence:

- **Monthly:** review the user-agent strings in `scripts/robots_inspect.py` against each provider's published bot docs (OpenAI, Perplexity, Anthropic, Bing).
- **Quarterly:** review `docs/rules.yaml` for new Google deprecations (Search Central blog) and new Core Web Vitals (`web.dev/vitals`).
- **On every release:** run the verification test plan in the project's main plan document.
