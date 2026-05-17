#!/usr/bin/env python3
"""
Evaluate docs/rules.yaml against a gathered facts.json, emit audit.json.

Output schema (audit.json):
{
  "site_url": "...",
  "rules_total": N,
  "verdict": "not_ready" | "competitive" | "leading",
  "summary": {
    "must": {"pass": x, "fail": y, "needs_improvement": z, "skipped": s},
    "should": {...},
    "nice": {...}
  },
  "findings": [
    {
      "rule_id": "...",
      "category": "...",
      "severity": "must|should|nice",
      "scope": "per-site|per-page",
      "title": "...",
      "page_url": "..." | null,
      "status": "pass|fail|needs_improvement|nice_not_done|skipped",
      "observed": <value>,
      "threshold": <value>,
      "source_url": "https://...",
      "fix_hint": "...",
      "rationale": "..."
    },
    ...
  ]
}

Usage:
    python rules_engine.py --facts facts.json --rules docs/rules.yaml --out audit.json
"""

import argparse
import json
import re
import sys
from typing import Any, Optional

try:
    import yaml
except ImportError:
    print("Error: pip install pyyaml", file=sys.stderr)
    sys.exit(1)


SEVERITIES = ("must", "should", "nice")


class Skip(Exception):
    """Raised when a rule cannot be evaluated (e.g. fact missing)."""


def _get_fact(facts: dict, path: str) -> Any:
    if path not in facts:
        raise Skip(f"fact {path} not present")
    return facts[path]


def _op_check(op: str, observed: Any, check: dict) -> tuple[str, Any]:
    """Apply an op. Returns (status, threshold_value).

    status: pass | fail | needs_improvement
    """
    value = check.get("value")
    if op == "equals":
        return ("pass" if observed == value else "fail", value)
    if op == "not_equals":
        return ("pass" if observed != value else "fail", value)
    if op == "truthy":
        return ("pass" if observed else "fail", "truthy")
    if op == "falsy":
        return ("pass" if not observed else "fail", "falsy")
    if op == "exists":
        return ("pass" if observed not in (None, "", [], {}) else "fail", "exists")
    if op == "not_exists":
        return ("pass" if observed in (None, "", [], {}) else "fail", "absent")
    if op == "less_than_or_equal":
        # Supports the CWV-style good/needs_improvement banding when those keys
        # are present on the check.
        if observed is None:
            raise Skip("observed value is None")
        good = check.get("good")
        ni = check.get("needs_improvement")
        if good is not None:
            if observed <= good:
                return ("pass", good)
            if ni is not None and observed <= ni:
                return ("needs_improvement", ni)
            return ("fail", ni if ni is not None else good)
        if value is None:
            raise Skip("no threshold")
        return ("pass" if observed <= value else "fail", value)
    if op == "greater_than_or_equal":
        if observed is None:
            raise Skip("observed value is None")
        if value is None:
            raise Skip("no threshold")
        return ("pass" if observed >= value else "fail", value)
    if op == "none_of":
        values = check.get("values") or []
        if not isinstance(observed, list):
            raise Skip("none_of requires list observed")
        offenders = [v for v in observed if v in values]
        return ("pass" if not offenders else "fail", values)
    if op == "any_of":
        values = check.get("values") or []
        return ("pass" if observed in values else "fail", values)
    if op == "contains":
        return ("pass" if (value in (observed or [])) else "fail", value)
    if op == "not_contains":
        return ("pass" if (value not in (observed or [])) else "fail", value)
    if op == "length_at_most":
        if observed is None:
            raise Skip("observed None")
        return ("pass" if len(observed) <= value else "fail", value)
    if op == "length_at_least":
        if observed is None:
            raise Skip("observed None")
        return ("pass" if len(observed) >= value else "fail", value)
    if op == "matches_re":
        pat = re.compile(value or "")
        return ("pass" if observed and pat.search(observed) else "fail", value)
    raise Skip(f"unsupported op: {op}")


def _evaluate_rule(rule: dict, facts: dict, page_url: Optional[str] = None) -> dict:
    check = rule["check"]
    fact_path = check["fact"]
    try:
        observed = _get_fact(facts, fact_path)
    except Skip as e:
        return _finding(rule, page_url, "skipped", None, None, reason=str(e))
    try:
        status, threshold = _op_check(check["op"], observed, check)
    except Skip as e:
        return _finding(rule, page_url, "skipped", observed, None, reason=str(e))

    # NICE rules that fail become "nice_not_done" so they don't count as
    # blocking failures in the verdict logic.
    if rule["severity"] == "nice" and status == "fail":
        status = "nice_not_done"

    return _finding(rule, page_url, status, observed, threshold)


def _finding(rule: dict, page_url: Optional[str], status: str, observed: Any, threshold: Any, reason: Optional[str] = None) -> dict:
    return {
        "rule_id": rule["id"],
        "category": rule["category"],
        "severity": rule["severity"],
        "scope": rule["scope"],
        "title": rule["title"],
        "page_url": page_url,
        "status": status,
        "observed": observed,
        "threshold": threshold,
        "source_url": (rule.get("source") or {}).get("url"),
        "source_quote": (rule.get("source") or {}).get("quote"),
        "fix_hint": (rule.get("fix_hint") or "").strip(),
        "rationale": (rule.get("rationale") or "").strip(),
        "reason": reason,
    }


def _verdict(findings: list[dict]) -> tuple[str, dict]:
    summary = {s: {"pass": 0, "fail": 0, "needs_improvement": 0, "nice_not_done": 0, "skipped": 0} for s in SEVERITIES}
    for f in findings:
        b = summary[f["severity"]]
        b[f["status"]] = b.get(f["status"], 0) + 1

    must_fail = summary["must"]["fail"] + summary["must"]["needs_improvement"]
    should_total = sum(summary["should"][k] for k in ("pass", "fail", "needs_improvement"))
    should_pass = summary["should"]["pass"]
    should_ratio = (should_pass / should_total) if should_total else 1.0
    if must_fail > 0:
        verdict = "not_ready"
    elif should_ratio >= 0.8 and summary["should"]["fail"] == 0:
        verdict = "leading" if should_ratio == 1.0 else "competitive"
    elif should_ratio >= 0.8:
        verdict = "competitive"
    else:
        verdict = "competitive" if must_fail == 0 else "not_ready"
    return verdict, summary


def run(facts_doc: dict, rules: list[dict]) -> dict:
    site_facts = facts_doc.get("site", {})
    pages = facts_doc.get("pages", [])
    findings: list[dict] = []

    for rule in rules:
        if rule["scope"] == "per-site":
            findings.append(_evaluate_rule(rule, site_facts, page_url=None))
        elif rule["scope"] == "per-page":
            if not pages:
                findings.append(_finding(rule, None, "skipped", None, None, reason="no pages crawled"))
                continue
            for p in pages:
                findings.append(_evaluate_rule(rule, p.get("facts", {}), page_url=p.get("url")))
        else:
            findings.append(_finding(rule, None, "skipped", None, None, reason=f"unknown scope {rule['scope']}"))

    verdict, summary = _verdict(findings)
    return {
        "rules_total": len(rules),
        "site_url": (pages[0].get("url") if pages else None) or "(no entry)",
        "pages_audited": len(pages),
        "verdict": verdict,
        "summary": summary,
        "findings": findings,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--facts", required=True)
    p.add_argument("--rules", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    with open(args.facts, "r", encoding="utf-8") as f:
        facts_doc = json.load(f)
    with open(args.rules, "r", encoding="utf-8") as f:
        rules = yaml.safe_load(f)

    audit = run(facts_doc, rules)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2)
    s = audit["summary"]
    print(
        f"Verdict: {audit['verdict']}  |  "
        f"MUST pass/fail: {s['must']['pass']}/{s['must']['fail']}  |  "
        f"SHOULD pass/fail: {s['should']['pass']}/{s['should']['fail']}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
