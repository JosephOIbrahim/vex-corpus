"""Import Joy of VEX RAG records into vex-corpus JSONL format.

Reads the final JSONL from the vex_rag_pipeline, maps each record to the
VEXSample schema used by vex-corpus, and exports as a corpus JSONL file.

Usage:
    python scripts/import_joy_of_vex.py [--input PATH] [--output PATH] [--dry-run]
"""

import argparse
import hashlib
import json
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DEFAULT_INPUT = Path(__file__).resolve().parent.parent.parent / "vex_rag_pipeline" / "output" / "joy_of_vex_rag.jsonl"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "joy_of_vex_corpus.jsonl"

# ---------------------------------------------------------------------------
# Difficulty mapping
# ---------------------------------------------------------------------------
DIFFICULTY_MAP = {
    "beginner": 1,
    "intermediate": 3,
    "advanced": 5,
}

# ---------------------------------------------------------------------------
# Attribute classification helpers
# ---------------------------------------------------------------------------
_ATTR_READ_RE = re.compile(r"@(\w+)")
_ATTR_WRITE_RE = re.compile(r"@(\w+)\s*=")
_CHANNEL_RE = re.compile(r"ch[fivs]\(|chramp\(")


def classify_attributes(code):
    """Extract read/written attributes and channel references from VEX code."""
    written = set(_ATTR_WRITE_RE.findall(code))
    all_attrs = set(_ATTR_READ_RE.findall(code))
    read = all_attrs - written
    channels = [m.group() for m in _CHANNEL_RE.finditer(code)]
    return sorted(read), sorted(written), channels


def map_complexity(code, functions):
    """Estimate complexity from code length and function count."""
    lines = code.strip().count("\n") + 1
    if lines <= 2 and len(functions) <= 2:
        return "simple"
    if lines <= 8 and len(functions) <= 5:
        return "moderate"
    return "complex"


def convert_record(record):
    """Convert a Joy of VEX RAG record to vex-corpus VEXSample format."""
    code = record.get("code", "").strip()
    if not code:
        return None

    # Hash for dedup
    code_hash = hashlib.sha256(code.encode()).hexdigest()[:16]

    # Attributes
    attrs_read, attrs_written, channels = classify_attributes(code)

    # Functions -- filter out attribute references (start with @)
    functions = [f for f in record.get("vex_functions", []) if not f.startswith("@")]

    # Difficulty
    difficulty_str = record.get("difficulty", "intermediate")
    difficulty = DIFFICULTY_MAP.get(difficulty_str, 3)

    # Complexity
    complexity = map_complexity(code, functions)

    # Source provenance: YouTube URL
    source_file = record.get("youtube_url", record.get("source", ""))

    # Build the VEXSample-compatible dict
    sample = {
        "id": record.get("id", f"vex_{code_hash}"),
        "code": code,
        "source_file": source_file,
        "source_line": 0,
        "hash": code_hash,
        "stage": "complete",
        "classification": {
            "context": record.get("vex_context", "SOP"),
            "context_confidence": record.get("ocr_confidence", 0.8),
            "attributes_read": attrs_read,
            "attributes_written": attrs_written,
            "channels": channels,
            "functions": functions,
            "bugs": [],
            "complexity": complexity,
            "topic": record.get("category", record.get("topic", "")),
        },
        "generation": {
            "prompt": record.get("topic", ""),
            "alternative_prompts": record.get("related_concepts", []),
            "explanation": record.get("explanation", ""),
            "difficulty": difficulty,
        },
        "quality": {
            "flagged_for_review": record.get("needs_review", False),
            "review_reason": "auto-imported from Joy of VEX pipeline" if record.get("needs_review") else "",
        },
    }
    return sample


def main():
    parser = argparse.ArgumentParser(
        description="Import Joy of VEX data into vex-corpus format."
    )
    parser.add_argument(
        "--input", type=Path, default=DEFAULT_INPUT,
        help="Path to joy_of_vex_rag.jsonl",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT,
        help="Output JSONL path",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print stats without writing",
    )
    args = parser.parse_args()

    # Load
    print(f"Reading {args.input} ...")
    records = []
    with open(args.input, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    print(f"  Loaded {len(records)} records")

    # Convert
    samples = []
    skipped = 0
    for r in records:
        sample = convert_record(r)
        if sample:
            samples.append(sample)
        else:
            skipped += 1

    # Deduplicate by hash
    seen_hashes = set()
    deduped = []
    for s in samples:
        h = s["hash"]
        if h not in seen_hashes:
            seen_hashes.add(h)
            deduped.append(s)

    print(f"  Converted: {len(samples)}, Skipped: {skipped}, After dedup: {len(deduped)}")

    # Difficulty distribution
    diff_counts = {}
    for s in deduped:
        d = s["generation"]["difficulty"]
        diff_counts[d] = diff_counts.get(d, 0) + 1
    print(f"  Difficulty distribution: {dict(sorted(diff_counts.items()))}")

    # Review flags
    review_count = sum(1 for s in deduped if s["quality"]["flagged_for_review"])
    print(f"  Flagged for review: {review_count}")

    if args.dry_run:
        print(f"\n[DRY RUN] Would write {len(deduped)} samples to {args.output}")
        return

    # Write
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        for s in deduped:
            f.write(json.dumps(s, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"\nWrote {len(deduped)} samples to {args.output}")


if __name__ == "__main__":
    main()
