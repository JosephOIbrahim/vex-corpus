#!/usr/bin/env python3
"""Ingest the Houdini 21 best-practices guide into corpus chunks.

Wires ``docs/HOUDINI21_BEST_PRACTICES.md`` into the corpus: every fenced
``vex`` code block becomes a ChunkV2, tagged with the workflow domain and VEX
context inferred from its section, stamped MIT, and run through the quality
gate. Output: ``output/best_practices/best_practices_corpus.jsonl``, which
``scripts/build_corpus.py`` folds into the canonical corpus.

Grouping for Synapse:
  - Code from an H21 domain section (MPM, look dev, ...) gets ``llm_topic ==
    domain`` so it joins that domain's reference file.
  - General VEX best-practices and anti-patterns get ``llm_topic ==
    "best_practices"`` so they form their own reference file.

Usage:
    python scripts/ingest_best_practices.py
    python scripts/ingest_best_practices.py --static-only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.schema import REDISTRIBUTABLE_LICENSES, ChunkV2, CodeBlock  # noqa: E402
from scripts.quality.verify_vex import verify_code  # noqa: E402

GUIDE = PROJECT_ROOT / "docs" / "HOUDINI21_BEST_PRACTICES.md"
OUTPUT = PROJECT_ROOT / "output" / "best_practices" / "best_practices_corpus.jsonl"

SOURCE_ID = "houdini21-best-practices"
SOURCE_AUTHORITY = 0.9
LICENSE = "MIT"
ATTRIBUTION = "vex-corpus maintainers -- Houdini 21 Best Practices guide (MIT)"
GUIDE_URL = "docs/HOUDINI21_BEST_PRACTICES.md"

# Section heading text -> (domain, default vex_context, content_type, llm_topic)
# Matched case-insensitively as a substring of the active "##" heading.
SECTION_MAP = [
    ("anti-pattern",        ("fundamentals", "sop",      "troubleshooting", "best_practices")),
    ("procedural modeling", ("procedural_modeling", "sop", "pattern", "procedural_modeling")),
    ("mpm",                 ("mpm", "solver",            "pattern", "mpm")),
    ("simulation",          ("mpm", "solver",            "pattern", "mpm")),
    ("look development",    ("look_development", "material", "pattern", "look_development")),
    ("lighting",            ("lighting", "sop",          "pattern", "lighting")),
    ("solaris",             ("solaris", "sop",           "pattern", "solaris")),
    ("usd",                 ("solaris", "sop",           "pattern", "solaris")),
    ("apex",                ("apex", "apex",             "pattern", "apex")),
    ("tops",                ("tops", "sop",              "pattern", "tops")),
    ("pdg",                 ("tops", "sop",              "pattern", "tops")),
    ("copernicus",          ("look_development", "sop",  "pattern", "look_development")),
]
DEFAULT_MAP = ("fundamentals", "sop", "pattern", "best_practices")

_H2 = re.compile(r"^##\s+(.*?)\s*$")
_H3 = re.compile(r"^###\s+(.*?)\s*$")
_H4 = re.compile(r"^####\s+(.*?)\s*$")
_BOLD_LEAD = re.compile(r"^\*\*(.+?)\*\*")
_FENCE_OPEN = re.compile(r"^```vex\s*$")
_FENCE_CLOSE = re.compile(r"^```\s*$")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_") or "section"


def _map_section(h2: str, h3: str = "") -> tuple[str, str, str, str]:
    # The domain often lives in the subsection (e.g. "6.2 ... MPM"); give h3
    # priority over h2 but consider both.
    low = f"{h3} {h2}".lower()
    for needle, result in SECTION_MAP:
        if needle in low:
            return result
    return DEFAULT_MAP


def _refine_context(domain: str, default_ctx: str, code: str) -> list[str]:
    if domain == "apex":
        return ["apex"]
    if "f@TimeInc" in code or "inside a solver" in code.lower():
        return ["solver"]
    if domain == "look_development" and ("Cf" in code or "snippet" in code.lower()):
        return ["material"]
    return [default_ctx]


def _code_doc(code: str) -> str:
    """First run of leading // comment lines, as a fallback explanation."""
    out = []
    for line in code.splitlines():
        s = line.strip()
        if s.startswith("//"):
            out.append(s.lstrip("/ ").rstrip())
        elif out:
            break
    return " ".join(out).strip()


def parse_guide(text: str) -> list[dict]:
    """Extract one record per fenced ``vex`` block."""
    records: list[dict] = []
    h2 = h3 = ""
    lead = ""              # most recent bold lead-in (anti-pattern titles)
    prose: list[str] = []  # recent prose lines for explanation context
    seq = 0

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]

        if m := _H2.match(line):
            h2, h3, lead, prose = m.group(1), "", "", []
        elif m := _H3.match(line):
            h3, lead, prose = m.group(1), "", []
        elif m := _H4.match(line):
            h3, lead, prose = m.group(1), "", []
        elif m := _BOLD_LEAD.match(line.strip()):
            lead = m.group(1).rstrip(":-— ").strip()
            prose.append(line.strip())
        elif _FENCE_OPEN.match(line.strip()):
            body: list[str] = []
            i += 1
            while i < len(lines) and not _FENCE_CLOSE.match(lines[i].strip()):
                body.append(lines[i])
                i += 1
            code = "\n".join(body).strip()
            if code:
                seq += 1
                domain, dctx, ctype, topic = _map_section(h2, h3)
                title = lead or h3 or h2 or "best practice"
                explanation = (prose[-1] if prose and not prose[-1].startswith("**")
                               else "") or _code_doc(code)
                records.append({
                    "seq": seq,
                    "section_h2": h2,
                    "title": title,
                    "code": code,
                    "explanation": explanation,
                    "domain": domain,
                    "vex_context": _refine_context(domain, dctx, code),
                    "content_type": ctype,
                    "llm_topic": topic,
                    "subcontext": "best_practice",
                })
            lead = ""
        elif line.strip():
            prose.append(line.strip())
            prose = prose[-4:]

        i += 1

    return records


def record_to_dict(rec: dict, static_only: bool) -> dict:
    """Convert a parsed record into a ChunkV2 export dict (+ llm_topic)."""
    code = rec["code"]
    ctx = rec["vex_context"][0] if rec["vex_context"] else "sop"
    res = verify_code(code, ctx, static_only=static_only)
    chunk = ChunkV2(
        id=f"h21bp_{_slug(rec['section_h2'])}_{rec['seq']:03d}",
        content=rec["explanation"],
        code_blocks=[CodeBlock(code=code, line_context="best-practices guide",
                               is_complete=res.passed)],
        content_type=rec["content_type"],
        difficulty="intermediate",
        vex_context=rec["vex_context"],
        subcontext=rec["subcontext"],
        domain=rec["domain"],
        source_id=SOURCE_ID,
        source_url=GUIDE_URL,
        source_authority=SOURCE_AUTHORITY,
        title=rec["title"],
        section=f"Best Practices / {rec['section_h2']}",
        license=LICENSE,
        attribution=ATTRIBUTION,
        houdini_version_min="21.0",
        prompt=rec["title"],
        alternative_prompts=[rec["domain"].replace("_", " "),
                             "houdini 21 best practice"],
        explanation=rec["explanation"],
        verified=res.verified,
        verification_method=res.method,
        verified_houdini_build=res.houdini_build,
        validation_warnings=res.warnings if res.passed else res.errors,
        flagged_for_review=not res.passed,
        review_reason="" if res.passed else "; ".join(res.errors),
    )
    # llm_topic is not a ChunkV2 field; attach it for the Synapse sync grouping.
    d = chunk.to_dict()
    d["llm_topic"] = rec["llm_topic"]
    return d


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest the H21 best-practices guide")
    ap.add_argument("--static-only", action="store_true")
    args = ap.parse_args()

    assert LICENSE in REDISTRIBUTABLE_LICENSES
    if not GUIDE.exists():
        print(f"guide not found: {GUIDE}", file=sys.stderr)
        return 2

    records = parse_guide(GUIDE.read_text(encoding="utf-8"))
    rows = [record_to_dict(r, args.static_only) for r in records]

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8") as f:
        for d in rows:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    by_topic: dict[str, int] = {}
    for d in rows:
        by_topic[d["llm_topic"]] = by_topic.get(d["llm_topic"], 0) + 1
    print(f"Ingested {len(rows)} best-practice chunks -> "
          f"{OUTPUT.relative_to(PROJECT_ROOT)}")
    print("  by topic: " + ", ".join(f"{k}={v}" for k, v in sorted(by_topic.items())))
    print(f"  method: {rows[0]['verification_method'] if rows else 'n/a'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
