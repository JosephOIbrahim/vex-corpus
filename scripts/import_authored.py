#!/usr/bin/env python3
"""Ingest authored samples into the corpus as verified ChunkV2 records.

Reads the per-domain JSONL under ``data/authored/`` (produced by
``scripts/authoring/build_authored.py``), stamps license/source/verification
metadata, runs the quality gate (``scripts/quality/verify_vex.py``), and writes
ChunkV2 chunks to ``output/authored/authored_corpus.jsonl``.

The verification method recorded depends on the environment:
  - Houdini present  -> ``hython-cook``; chunks that cook clean get verified=True
    and verified_houdini_build set to the actual build.
  - Houdini absent   -> ``static-lint``; verified stays False (honest: we only
    claim "verified" for code that actually cooked on the target build).

Usage:
    python scripts/import_authored.py
    python scripts/import_authored.py --static-only
    python scripts/import_authored.py --merge        # also append to merged_corpus
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.schema import (  # noqa: E402
    REDISTRIBUTABLE_LICENSES,
    ChunkV2,
    CodeBlock,
)
from scripts.quality.verify_vex import verify_code  # noqa: E402

AUTHORED_DIR = PROJECT_ROOT / "data" / "authored"
OUTPUT_PATH = PROJECT_ROOT / "output" / "authored" / "authored_corpus.jsonl"
MERGED_PATH = PROJECT_ROOT / "output" / "corpus" / "merged_corpus.jsonl"

SOURCE_ID = "authored-h21-samples"
SOURCE_AUTHORITY = 0.85
LICENSE = "MIT"
ATTRIBUTION = "vex-corpus authored samples (https://github.com/JosephOIbrahim/vex-corpus), MIT"


def _to_chunk(rec: dict, static_only: bool) -> ChunkV2:
    code = rec["code"]
    ctx = rec.get("vex_context", ["sop"])
    primary_ctx = ctx[0] if ctx else "sop"

    res = verify_code(code, primary_ctx, static_only=static_only)

    chunk = ChunkV2(
        id=rec["id"],
        content=rec["explanation"],
        code_blocks=[CodeBlock(code=code, line_context=rec.get("subcontext", ""),
                               is_complete=True)],
        content_type=rec.get("content_type", "pattern"),
        difficulty=rec.get("difficulty", "intermediate"),
        vex_context=ctx,
        subcontext=rec.get("subcontext", ""),
        domain=rec.get("domain", ""),
        source_id=SOURCE_ID,
        source_url="",
        source_authority=SOURCE_AUTHORITY,
        title=rec.get("title", ""),
        section=f"Authored / {rec.get('domain', '')}",
        license=LICENSE,
        attribution=ATTRIBUTION,
        functions_referenced=rec.get("functions_referenced", []),
        attributes_read=rec.get("attributes_read", []),
        attributes_written=rec.get("attributes_written", []),
        houdini_version_min=rec.get("houdini_version_min", "21.0"),
        houdini_version_notes=rec.get("houdini_version_notes", ""),
        prompt=rec.get("prompt", ""),
        alternative_prompts=rec.get("alternative_prompts", []),
        explanation=rec.get("explanation", ""),
        verified=res.verified,
        verification_method=res.method,
        verified_houdini_build=res.houdini_build,
        validation_warnings=res.warnings if res.passed else res.errors,
        flagged_for_review=not res.passed,
        review_reason="" if res.passed else "; ".join(res.errors),
    )
    return chunk


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest authored samples to ChunkV2")
    ap.add_argument("--static-only", action="store_true",
                    help="skip Houdini cook even if available")
    ap.add_argument("--merge", action="store_true",
                    help="also append (deduped) to output/corpus/merged_corpus.jsonl")
    args = ap.parse_args()

    assert LICENSE in REDISTRIBUTABLE_LICENSES, f"{LICENSE} not redistributable"

    files = sorted(AUTHORED_DIR.glob("*.jsonl"))
    if not files:
        print(f"No authored JSONL in {AUTHORED_DIR}. Run build_authored.py first.",
              file=sys.stderr)
        return 2

    chunks: list[ChunkV2] = []
    for path in files:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    chunks.append(_to_chunk(json.loads(line), args.static_only))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")

    verified = sum(1 for c in chunks if c.verified)
    flagged = sum(1 for c in chunks if c.flagged_for_review)
    method = chunks[0].verification_method if chunks else "n/a"
    by_domain: dict[str, int] = {}
    for c in chunks:
        by_domain[c.domain] = by_domain.get(c.domain, 0) + 1

    print(f"Ingested {len(chunks)} authored chunks -> {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")
    print(f"  verification method: {method}")
    print(f"  verified (cooked on Houdini): {verified}")
    print(f"  flagged for review:           {flagged}")
    print("  by domain: " + ", ".join(f"{k}={v}" for k, v in sorted(by_domain.items())))

    if args.merge:
        existing_checksums = set()
        if MERGED_PATH.exists():
            with MERGED_PATH.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        existing_checksums.add(json.loads(line).get("checksum", ""))
        added = 0
        with MERGED_PATH.open("a", encoding="utf-8") as f:
            for c in chunks:
                if c.checksum not in existing_checksums:
                    f.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")
                    existing_checksums.add(c.checksum)
                    added += 1
        print(f"  merged {added} new chunks into {MERGED_PATH.relative_to(PROJECT_ROOT)} "
              f"(skipped {len(chunks) - added} dupes)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
