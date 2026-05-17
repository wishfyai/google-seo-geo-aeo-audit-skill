#!/usr/bin/env python3
"""
Render an audit.json into a human-readable Markdown report.

Usage:
    python render_report.py audit.json --out audit.md
"""

import argparse
import json
from collections import defaultdict


VERDICT_BADGE = {
    "not_ready": "**Not Ready** — at least one MUST rule is failing. Fix these before anything else.",
    "competitive": "**Competitive** — all MUST rules pass, the site is eligible across Google and other AI engines. Iterate on the SHOULD list.",
    "leading": "**Leading** — all MUST and SHOULD rules pass. Focus on content quality, entity authority, and ongoing monitoring.",
}

STATUS_LABEL = {
    "pass": "✓ pass",
    "fail": "✗ fail",
    "needs_improvement": "△ needs improvement",
    "nice_not_done": "○ not done (nice-to-have)",
    "skipped": "— skipped",
}


def _format_observed(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.3f}"
    if isinstance(value, list):
        if not value:
            return "[]"
        return ", ".join(str(v) for v in value[:5]) + (" …" if len(value) > 5 else "")
    return str(value)


def render(audit: dict) -> str:
    out: list[str] = []
    out.append(f"# google-audit report\n")
    out.append(f"**Verdict:** {VERDICT_BADGE[audit['verdict']]}\n")
    out.append(f"- **Site:** `{audit.get('site_url', '—')}`")
    out.append(f"- **Pages audited:** {audit.get('pages_audited', 0)}")
    out.append(f"- **Rules evaluated:** {audit['rules_total']}\n")

    out.append("## Summary by severity\n")
    out.append("| Severity | Pass | Fail | Needs improvement | Not done (nice) | Skipped |")
    out.append("|---|---:|---:|---:|---:|---:|")
    for sev in ("must", "should", "nice"):
        s = audit["summary"][sev]
        out.append(
            f"| **{sev.upper()}** | {s.get('pass',0)} | {s.get('fail',0)} | "
            f"{s.get('needs_improvement',0)} | {s.get('nice_not_done',0)} | {s.get('skipped',0)} |"
        )
    out.append("")

    findings = audit["findings"]

    # Failing-MUST callout
    must_failures = [f for f in findings if f["severity"] == "must" and f["status"] in ("fail", "needs_improvement")]
    if must_failures:
        out.append("## Failing MUST rules (fix first)\n")
        for f in must_failures:
            out.append(_render_finding(f))
        out.append("")

    # Group remaining findings by category, then severity (MUST → SHOULD → NICE)
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for f in findings:
        by_cat[f["category"]].append(f)

    out.append("## Findings by category\n")
    for cat in sorted(by_cat.keys()):
        out.append(f"### {cat}\n")
        cat_findings = sorted(
            by_cat[cat],
            key=lambda x: (("must", "should", "nice").index(x["severity"]), x["rule_id"], x.get("page_url") or ""),
        )
        for f in cat_findings:
            out.append(_render_finding(f, compact=True))
        out.append("")

    out.append("---\n")
    out.append("_Every finding cites a Google or recognized provider doc. If a recommendation in the audit ever drifts from Google's published guidance, please open a PR against `docs/rules.yaml`._\n")
    return "\n".join(out)


def _render_finding(f: dict, compact: bool = False) -> str:
    label = STATUS_LABEL.get(f["status"], f["status"])
    title = f"**[{f['severity'].upper()}] {label}** — {f['title']}"
    if f.get("page_url") and f["scope"] == "per-page":
        title += f"  \n_Page:_ `{f['page_url']}`"
    lines = [title]
    if not compact:
        lines.append(f"_Rule:_ `{f['rule_id']}`")
    lines.append(
        f"_Observed:_ `{_format_observed(f.get('observed'))}` · _Threshold:_ `{_format_observed(f.get('threshold'))}`"
    )
    if f.get("source_url"):
        lines.append(f"_Source:_ [{f['source_url']}]({f['source_url']})")
    if f["status"] in ("fail", "needs_improvement") and f.get("fix_hint"):
        lines.append(f"_Fix:_ {f['fix_hint']}")
    if f.get("reason"):
        lines.append(f"_Note:_ {f['reason']}")
    return "\n\n".join(lines) + "\n"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("audit_json")
    p.add_argument("--out", required=True)
    args = p.parse_args()
    with open(args.audit_json, "r", encoding="utf-8") as f:
        audit = json.load(f)
    md = render(audit)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
