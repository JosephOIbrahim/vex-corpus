"""Merge all per-source outputs into a unified corpus.

Reads JSONL files from output/*/, merges into output/corpus/,
generates content checksums for dedup detection, reports statistics,
and flags near-duplicate chunks using MinHash.

Usage:
    python scripts/merge_sources.py
    python scripts/merge_sources.py --no-dedup
    python scripts/merge_sources.py --threshold 0.9
"""

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from datasketch import MinHash, MinHashLSH

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.schema import PIPELINE_VERSION, ChunkV2

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OUTPUT_ROOT = PROJECT_ROOT / "output"
CORPUS_DIR = OUTPUT_ROOT / "corpus"

# Source directories to scan for JSONL files
SOURCE_DIRS = [
    OUTPUT_ROOT / "cgwiki",
    OUTPUT_ROOT / "sidefx_reference",
    OUTPUT_ROOT / "local",
]

# Also include the Joy of VEX corpus from data/
JOY_OF_VEX_CORPUS = PROJECT_ROOT / "data" / "joy_of_vex_corpus.jsonl"

# MinHash parameters
MINHASH_NUM_PERM = 128
DEFAULT_SIMILARITY_THRESHOLD = 0.85


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_joy_of_vex() -> list[dict]:
    """Load Joy of VEX corpus and migrate to v2 format."""
    from pipeline.schema import migrate_v1_sample

    if not JOY_OF_VEX_CORPUS.exists():
        return []

    chunks = []
    with open(JOY_OF_VEX_CORPUS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            chunk = migrate_v1_sample(raw)
            chunks.append(chunk.to_dict())

    return chunks


def load_source_dir(source_dir: Path) -> list[dict]:
    """Load all JSONL files from a source directory."""
    if not source_dir.exists():
        return []

    chunks = []
    for jsonl_file in sorted(source_dir.glob("*.jsonl")):
        # Skip *_all.jsonl to avoid double-counting
        # (per-page files + combined file)
        if jsonl_file.stem.endswith("_all"):
            # Use the _all file as the single source
            with open(jsonl_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        chunks.append(json.loads(line))
            return chunks

    # No _all file -- load individual files
    for jsonl_file in sorted(source_dir.glob("*.jsonl")):
        with open(jsonl_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    chunks.append(json.loads(line))

    return chunks


def load_all_sources() -> list[dict]:
    """Load chunks from all sources."""
    all_chunks = []

    # Joy of VEX (migrated)
    jov = load_joy_of_vex()
    if jov:
        print(f"  joy-of-vex-youtube: {len(jov)} chunks")
        all_chunks.extend(jov)

    # Other source directories
    for source_dir in SOURCE_DIRS:
        chunks = load_source_dir(source_dir)
        if chunks:
            source_name = source_dir.name
            print(f"  {source_name}: {len(chunks)} chunks")
            all_chunks.extend(chunks)

    return all_chunks


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _text_for_minhash(chunk: dict) -> str:
    """Extract text content for MinHash comparison."""
    parts = [chunk.get("content", "")]
    for cb in chunk.get("code_blocks", []):
        if isinstance(cb, dict):
            parts.append(cb.get("code", ""))
        else:
            parts.append(str(cb))
    # Also include v1 code field
    if "code" in chunk:
        parts.append(chunk["code"])
    return " ".join(parts).lower()


def _make_minhash(text: str) -> MinHash:
    """Create a MinHash from text using word n-grams."""
    m = MinHash(num_perm=MINHASH_NUM_PERM)
    # Use word 3-grams for better similarity detection
    words = text.split()
    for i in range(len(words) - 2):
        ngram = " ".join(words[i:i+3])
        m.update(ngram.encode("utf-8"))
    # Also add individual words for short texts
    if len(words) < 10:
        for w in words:
            m.update(w.encode("utf-8"))
    return m


def find_near_duplicates(chunks: list[dict], threshold: float) -> list[dict]:
    """Find near-duplicate chunk pairs using MinHash LSH.

    Returns list of dicts: {chunk_a_id, chunk_b_id, similarity, recommendation}
    """
    print(f"\nRunning deduplication (threshold={threshold})...")

    lsh = MinHashLSH(threshold=threshold, num_perm=MINHASH_NUM_PERM)
    minhashes = {}

    for chunk in chunks:
        cid = chunk.get("id", "")
        if not cid:
            continue
        text = _text_for_minhash(chunk)
        if len(text.split()) < 3:
            continue  # Skip very short chunks
        mh = _make_minhash(text)
        minhashes[cid] = mh
        try:
            lsh.insert(cid, mh)
        except ValueError:
            pass  # Duplicate key

    # Find pairs
    seen_pairs = set()
    duplicates = []

    for cid, mh in minhashes.items():
        candidates = lsh.query(mh)
        for other_id in candidates:
            if other_id == cid:
                continue
            pair = tuple(sorted([cid, other_id]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            # Calculate exact Jaccard similarity
            sim = minhashes[cid].jaccard(minhashes[other_id])
            if sim >= threshold:
                # Determine recommendation based on source authority
                chunk_a = next((c for c in chunks if c.get("id") == cid), {})
                chunk_b = next((c for c in chunks if c.get("id") == other_id), {})
                auth_a = chunk_a.get("source_authority", 0)
                auth_b = chunk_b.get("source_authority", 0)

                if auth_a >= auth_b:
                    recommendation = f"KEEP {cid}, LINK {other_id}"
                else:
                    recommendation = f"KEEP {other_id}, LINK {cid}"

                if sim > 0.95:
                    recommendation = recommendation.replace("LINK", "DROP")

                duplicates.append({
                    "chunk_a_id": pair[0],
                    "chunk_b_id": pair[1],
                    "similarity": round(sim, 4),
                    "recommendation": recommendation,
                })

    print(f"  Found {len(duplicates)} near-duplicate pairs")
    return duplicates


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def compute_stats(chunks: list[dict]) -> dict:
    """Compute corpus statistics."""
    by_source = Counter()
    by_type = Counter()
    by_difficulty = Counter()
    functions = set()
    with_code = 0
    total_code_blocks = 0
    total_content_len = 0

    for c in chunks:
        by_source[c.get("source_id", "unknown")] += 1
        by_type[c.get("content_type", "unknown")] += 1
        by_difficulty[c.get("difficulty", "unknown")] += 1

        code_blocks = c.get("code_blocks", [])
        if code_blocks:
            with_code += 1
            total_code_blocks += len(code_blocks)

        for f in c.get("functions_referenced", []):
            functions.add(f)

        total_content_len += len(c.get("content", ""))
        for cb in code_blocks:
            if isinstance(cb, dict):
                total_content_len += len(cb.get("code", ""))

    avg_len = total_content_len // max(len(chunks), 1)

    return {
        "total_chunks": len(chunks),
        "by_source": dict(sorted(by_source.items())),
        "by_content_type": dict(sorted(by_type.items())),
        "by_difficulty": dict(sorted(by_difficulty.items())),
        "with_code": with_code,
        "total_code_blocks": total_code_blocks,
        "unique_functions": len(functions),
        "avg_chunk_length": avg_len,
    }


def print_stats(stats: dict):
    """Print formatted statistics."""
    print(f"\n{'=' * 55}")
    print(f" CORPUS STATISTICS")
    print(f"{'=' * 55}")
    print(f" Total chunks: {stats['total_chunks']}")
    print()
    print(f" By Source:")
    for source, count in stats["by_source"].items():
        pct = count * 100 / max(stats["total_chunks"], 1)
        bar = "#" * int(pct / 3)
        print(f"   {source:25s} {bar:20s} {count:4d} ({pct:.1f}%)")
    print()
    print(f" By Content Type:")
    for ct, count in stats["by_content_type"].items():
        print(f"   {ct}: {count}")
    print()
    print(f" By Difficulty:")
    for d, count in stats["by_difficulty"].items():
        print(f"   {d}: {count}")
    print()
    print(f" Chunks with code: {stats['with_code']} ({stats['with_code']*100/max(stats['total_chunks'],1):.1f}%)")
    print(f" Total code blocks: {stats['total_code_blocks']}")
    print(f" Unique functions referenced: {stats['unique_functions']}")
    print(f" Avg chunk length: {stats['avg_chunk_length']} chars")
    print(f"{'=' * 55}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def merge_all(
    run_dedup: bool = True,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    output_dir: Path | None = None,
) -> list[dict]:
    """Merge all sources into a unified corpus."""
    output_dir = output_dir or CORPUS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Loading sources...")
    all_chunks = load_all_sources()

    if not all_chunks:
        print("No chunks found.")
        return []

    # Exact dedup by checksum
    seen_checksums = set()
    deduped = []
    exact_dups = 0
    for c in all_chunks:
        cs = c.get("checksum", "")
        if cs and cs in seen_checksums:
            exact_dups += 1
            continue
        if cs:
            seen_checksums.add(cs)
        deduped.append(c)

    if exact_dups:
        print(f"\n  Removed {exact_dups} exact duplicates (by checksum)")
    all_chunks = deduped

    # Near-duplicate detection
    duplicates = []
    if run_dedup and len(all_chunks) > 1:
        duplicates = find_near_duplicates(all_chunks, threshold)

    # Compute stats
    stats = compute_stats(all_chunks)
    print_stats(stats)

    # Write merged corpus
    corpus_file = output_dir / "merged_corpus.jsonl"
    with open(corpus_file, "w", encoding="utf-8") as f:
        for chunk in all_chunks:
            f.write(json.dumps(chunk, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"\nCorpus written to: {corpus_file}")

    # Write manifest
    manifest = {
        "generated_at": datetime.now().isoformat(),
        "pipeline_version": PIPELINE_VERSION,
        "statistics": stats,
        "dedup_pairs": len(duplicates),
        "chunks": [
            {
                "id": c.get("id", ""),
                "source_id": c.get("source_id", ""),
                "content_type": c.get("content_type", ""),
                "difficulty": c.get("difficulty", ""),
                "title": c.get("title", ""),
            }
            for c in all_chunks
        ],
    }
    manifest_file = output_dir / "manifest.json"
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False, sort_keys=True)
    print(f"Manifest written to: {manifest_file}")

    # Write dedup report
    if duplicates:
        dedup_file = output_dir / "dedup_report.json"
        with open(dedup_file, "w", encoding="utf-8") as f:
            json.dump({
                "threshold": threshold,
                "pairs_found": len(duplicates),
                "pairs": duplicates,
            }, f, indent=2, ensure_ascii=False, sort_keys=True)
        print(f"Dedup report: {dedup_file} ({len(duplicates)} pairs)")

    # Write stats
    stats_file = output_dir / "stats.json"
    with open(stats_file, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, sort_keys=True)

    return all_chunks


def main():
    parser = argparse.ArgumentParser(description="Merge all source outputs into unified corpus")
    parser.add_argument("--no-dedup", action="store_true", help="Skip deduplication detection")
    parser.add_argument("--threshold", type=float, default=DEFAULT_SIMILARITY_THRESHOLD,
                        help=f"Similarity threshold for dedup (default: {DEFAULT_SIMILARITY_THRESHOLD})")
    parser.add_argument("--output", type=Path, help="Output directory")
    args = parser.parse_args()

    merge_all(
        run_dedup=not args.no_dedup,
        threshold=args.threshold,
        output_dir=args.output,
    )


if __name__ == "__main__":
    main()
