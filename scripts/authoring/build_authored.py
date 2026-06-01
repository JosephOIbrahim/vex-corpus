#!/usr/bin/env python3
"""Build per-domain authored-sample JSONL files from the catalog.

Reads ``scripts/authoring/catalog.py``, applies shared defaults, runs the
static linter on every sample (a fast sanity gate -- the full cook gate runs
later in ``import_authored.py`` when Houdini is present), and writes one JSONL
file per domain under ``data/authored/``.

Usage:
    python scripts/authoring/build_authored.py
    python scripts/authoring/build_authored.py --check   # lint only, no write
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.authoring.catalog import CATALOG  # noqa: E402
from scripts.quality.verify_vex import static_lint  # noqa: E402

OUTPUT_DIR = PROJECT_ROOT / "data" / "authored"

# Shared defaults applied to every sample unless it overrides them.
DEFAULTS = {
    "vex_context": ["sop"],
    "houdini_version_min": "21.0",
    "houdini_version_notes": "",
    "alternative_prompts": [],
    "functions_referenced": [],
    "attributes_read": [],
    "attributes_written": [],
}

REQUIRED_FIELDS = ["id", "title", "subcontext", "difficulty",
                   "content_type", "prompt", "code", "explanation"]


def _apply_defaults(sample: dict, domain: str) -> dict:
    out = dict(DEFAULTS)
    out.update(sample)
    out["domain"] = domain
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Build authored sample JSONL")
    ap.add_argument("--check", action="store_true",
                    help="lint only; do not write files")
    args = ap.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    total = 0
    lint_fail = 0
    lint_warn = 0
    seen_ids: set[str] = set()

    for key, (domain, samples) in CATALOG.items():
        records = []
        for s in samples:
            # Validate required fields up front
            missing = [f for f in REQUIRED_FIELDS if f not in s]
            if missing:
                print(f"  [ERROR] {s.get('id', '?')}: missing {missing}")
                lint_fail += 1
                continue

            if s["id"] in seen_ids:
                print(f"  [ERROR] duplicate id: {s['id']}")
                lint_fail += 1
                continue
            seen_ids.add(s["id"])

            rec = _apply_defaults(s, domain)
            ctx = rec["vex_context"][0] if rec["vex_context"] else "sop"
            res = static_lint(rec["code"], ctx)
            total += 1

            if not res.passed:
                lint_fail += 1
                print(f"  [FAIL] {rec['id']}: {'; '.join(res.errors)}")
            elif res.warnings:
                lint_warn += 1
                print(f"  [WARN] {rec['id']}: {'; '.join(res.warnings)}")
            records.append(rec)

        if not args.check:
            out_path = OUTPUT_DIR / f"{key}.jsonl"
            with out_path.open("w", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"  wrote {len(records):2d} -> {out_path.relative_to(PROJECT_ROOT)}")

    print(f"\n{total} samples, {lint_fail} lint failures, {lint_warn} warnings")
    return 1 if lint_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
