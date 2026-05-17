#!/usr/bin/env bash
# One-shot orchestration of the full google-audit pipeline.
# Designed to be invoked by SKILL.md OR run manually from the command line.
#
# Usage:
#   run_audit.sh <url> [--max-pages N] [--out DIR] [--no-psi] [--no-lighthouse]
#
# Examples:
#   run_audit.sh https://example.com
#   run_audit.sh example.com --max-pages 10
#   run_audit.sh https://example.com --out /tmp/example-audit --no-lighthouse

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

URL=""
MAX_PAGES=50
OUT=""
USE_PSI=1
USE_LIGHTHOUSE=1
PY="${PYTHON:-python3}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --max-pages) MAX_PAGES="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --no-psi) USE_PSI=0; shift ;;
    --no-lighthouse) USE_LIGHTHOUSE=0; shift ;;
    --python) PY="$2"; shift 2 ;;
    -h|--help)
      grep -E '^# ' "${BASH_SOURCE[0]}" | sed 's/^# //'
      exit 0
      ;;
    *) URL="$1"; shift ;;
  esac
done

if [[ -z "$URL" ]]; then
  echo "error: provide a URL or domain" >&2
  exit 2
fi

# Normalize URL — add https:// if scheme missing
if [[ "$URL" != http://* && "$URL" != https://* ]]; then
  URL="https://$URL"
fi

OUT="${OUT:-/tmp/google-audit-$(date +%s)}"
mkdir -p "$OUT"

log() { printf '\033[1;36m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
fail() { printf '\033[1;31m[fail]\033[0m %s\n' "$*" >&2; exit 1; }

# Verify Python deps quickly
"$PY" -c "import yaml, requests, bs4, lxml" 2>/dev/null \
  || fail "missing Python deps. Run: $PY -m pip install -r $SKILL_DIR/requirements.txt"

log "google-audit pipeline starting"
log "  URL=$URL"
log "  MAX_PAGES=$MAX_PAGES"
log "  OUT=$OUT"
log "  SKILL_DIR=$SKILL_DIR"

# Step 0 — prerequisites
log "[0/6] checking PSI / CrUX credentials"
"$PY" "$SKILL_DIR/scripts/google_auth.py" --check 2>&1 | sed 's/^/      /' || true

# Step 1 — crawl
log "[1/6] crawl (max $MAX_PAGES pages)"
"$PY" "$SKILL_DIR/scripts/crawl_site.py" "$URL" --max-pages "$MAX_PAGES" --out "$OUT" \
  || fail "crawl failed"
PAGE_COUNT=$("$PY" -c "import json; print(len(json.load(open('$OUT/crawl.json'))['pages']))")
log "      discovered $PAGE_COUNT pages"

# Step 2 — site-level robots inspection
log "[2/6] robots.txt + AI-bot allow-list"
"$PY" "$SKILL_DIR/scripts/robots_inspect.py" "$URL" --out "$OUT/robots.json" \
  || fail "robots_inspect failed"
curl -s -L --max-time 10 "$URL/llms.txt" -o "$OUT/llms_txt.txt" 2>/dev/null || true

# Step 3 — per-page facts (sequential to keep PSI rate-limit safe)
log "[3/6] per-page: fetch + parse + schema_validate ($USE_PSI=PSI, $USE_LIGHTHOUSE=Lighthouse)"
i=0
"$PY" -c "import json; [print(p['url']) for p in json.load(open('$OUT/crawl.json'))['pages']]" \
| while IFS= read -r PAGE_URL; do
  PAGE_DIR="$OUT/pages/$(printf '%03d' "$i")"
  mkdir -p "$PAGE_DIR"

  timeout 60 "$PY" "$SKILL_DIR/scripts/fetch_page.py" "$PAGE_URL" --output "$PAGE_DIR/page.html" 2>/dev/null \
    || { log "      [$i] skip — fetch failed for $PAGE_URL"; i=$((i+1)); continue; }
  timeout 30 "$PY" "$SKILL_DIR/scripts/parse_html.py" "$PAGE_DIR/page.html" --url "$PAGE_URL" --json > "$PAGE_DIR/parsed.json" 2>/dev/null

  # Inject _page_url + _viewport_present into parsed.json
  "$PY" - <<PY_EOF
import json
p = json.load(open("$PAGE_DIR/parsed.json"))
p["_page_url"] = "$PAGE_URL"
with open("$PAGE_DIR/page.html") as f:
    html = f.read()
p["_viewport_present"] = ('name="viewport"' in html) or ("name='viewport'" in html)
json.dump(p, open("$PAGE_DIR/parsed.json", "w"), indent=2)
PY_EOF

  timeout 30 "$PY" "$SKILL_DIR/scripts/schema_validate.py" "$PAGE_DIR/page.html" --out "$PAGE_DIR/schema.json" 2>/dev/null

  if [[ "$USE_PSI" == "1" && -n "${PAGESPEED_API_KEY:-}" ]]; then
    timeout 120 "$PY" "$SKILL_DIR/scripts/pagespeed_check.py" "$PAGE_URL" --strategy mobile --json > "$PAGE_DIR/psi.json" 2>/dev/null \
      || log "      [$i] psi skipped"
  fi

  if [[ "$USE_LIGHTHOUSE" == "1" ]] && command -v lighthouse >/dev/null 2>&1; then
    timeout 180 "$PY" "$SKILL_DIR/scripts/lighthouse_run.py" "$PAGE_URL" --out "$PAGE_DIR/lighthouse.json" 2>/dev/null \
      || log "      [$i] lighthouse skipped"
  fi

  log "      [$i] $PAGE_URL"
  i=$((i+1))
done

# Step 4 — gather
log "[4/6] gather facts"
"$PY" "$SKILL_DIR/scripts/gather_facts.py" "$OUT" --out "$OUT/facts.json" \
  || fail "gather_facts failed"

# Step 5 — evaluate
log "[5/6] evaluate rules"
"$PY" "$SKILL_DIR/scripts/rules_engine.py" \
  --facts "$OUT/facts.json" \
  --rules "$SKILL_DIR/docs/rules.yaml" \
  --out "$OUT/audit.json" \
  || fail "rules_engine failed"

# Step 6 — render
log "[6/6] render markdown"
"$PY" "$SKILL_DIR/scripts/render_report.py" "$OUT/audit.json" --out "$OUT/audit.md" \
  || fail "render_report failed"

log "done"
log "  audit.json -> $OUT/audit.json"
log "  audit.md   -> $OUT/audit.md"
echo
head -3 "$OUT/audit.md"
echo
echo "Full report: $OUT/audit.md"
