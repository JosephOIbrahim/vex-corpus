"""Deduplication report generator for VEX corpus.

Compares all chunks using MinHash LSH and generates a report of
near-duplicate pairs with recommendations (KEEP / LINK / DROP).

This is a standalone quality tool. The merge_sources.py script already
does basic dedup during merge — this provides a deeper post-merge analysis
with configurable thresholds and detailed reporting.

Usage:
    python scripts/quality/dedup_report.py
    python scripts/quality/dedup_report.py --threshold 0.80
    python scripts/quality/dedup_report.py --dry-run
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from datasketch import MinHash, MinHashLSH

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MINHASH_NUM_PERM = 128
DEFAULT_THRESHOLD = 0.80  # Slightly lower than merge to catch more pairs


# ---------------------------------------------------------------------------
# MinHash utilities
# ---------------------------------------------------------------------------

def _text_for_chunk(chunk: dict) -> str:
    """Extract all text content from a chunk for comparison."""
    parts = [chunk.get("content", "")]
    for cb in chunk.get("code_blocks", []):
        if isinstance(cb, dict):
            parts.append(cb.get("code", ""))
        else:
            parts.append(str(cb))
    if chunk.get("code"):
        parts.append(chunk["code"])
    return " ".join(parts).lower().strip()


def _make_minhash(text: str) -> MinHash:
    """Create a MinHash from text using word trigrams."""
    m = MinHash(num_perm=MINHASH_NUM_PERM)
    words = text.split()
    for i in range(len(words) - 2):
        ngram = " ".join(words[i:i + 3])
        m.update(ngram.encode("utf-8"))
    if len(words) < 10:
        for w in words:
            m.update(w.encode("utf-8"))
    return m


# ---------------------------------------------------------------------------
# Dedup analysis
# ---------------------------------------------------------------------------

def analyze_duplicates(chunks: list[dict], threshold: float) -> dict:
    """Find near-duplicate pairs and generate recommendations.

    Returns a report dict with pairs, clusters, and stats.
    """
    print(f"Building MinHash index for {len(chunks)} chunks (threshold={threshold})...")

    lsh = MinHashLSH(threshold=threshold, num_perm=MINHASH_NUM_PERM)
    minhashes = {}
    chunk_map = {}
    skipped = 0

    for chunk in chunks:
        cid = chunk.get("id", "")
        if not cid:
            continue
        text = _text_for_chunk(chunk)
        if len(text.split()) < 5:
            skipped += 1
            continue
        mh = _make_minhash(text)
        minhashes[cid] = mh
        chunk_map[cid] = chunk
        try:
            lsh.insert(cid, mh)
        except ValueError:
            pass  # Duplicate key

    print(f"  Indexed {len(minhashes)} chunks ({skipped} skipped as too short)")

    # Find pairs
    seen_pairs = set()
    pairs = []

    for cid, mh in minhashes.items():
        candidates = lsh.query(mh)
        for other_id in candidates:
            if other_id == cid:
                continue
            pair_key = tuple(sorted([cid, other_id]))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            sim = minhashes[cid].jaccard(minhashes[other_id])
            if sim < threshold:
                continue

            chunk_a = chunk_map.get(cid, {})
            chunk_b = chunk_map.get(other_id, {})

            # Determine recommendation
            auth_a = chunk_a.get("source_authority", 0)
            auth_b = chunk_b.get("source_authority", 0)
            source_a = chunk_a.get("source_id", "")
            source_b = chunk_b.get("source_id", "")

            if sim > 0.95:
                action = "DROP"  # Near-identical
            elif sim > 0.90:
                action = "MERGE"  # Very similar, merge content
            else:
                action = "LINK"  # Similar, cross-reference

            if auth_a >= auth_b:
                keep_id, drop_id = cid, other_id
            else:
                keep_id, drop_id = other_id, cid

            pairs.append({
                "chunk_a": cid,
                "chunk_b": other_id,
                "similarity": round(sim, 4),
                "action": action,
                "recommendation": f"KEEP {keep_id}, {action} {drop_id}",
                "source_a": source_a,
                "source_b": source_b,
                "authority_a": auth_a,
                "authority_b": auth_b,
            })

    # Sort by similarity descending
    pairs.sort(key=lambda p: -p["similarity"])

    # Build clusters (groups of mutually similar chunks)
    adjacency = defaultdict(set)
    for p in pairs:
        adjacency[p["chunk_a"]].add(p["chunk_b"])
        adjacency[p["chunk_b"]].add(p["chunk_a"])

    visited = set()
    clusters = []
    for node in adjacency:
        if node in visited:
            continue
        cluster = set()
        stack = [node]
        while stack:
            n = stack.pop()
            if n in visited:
                continue
            visited.add(n)
            cluster.add(n)
            stack.extend(adjacency[n] - visited)
        if len(cluster) > 1:
            clusters.append(sorted(cluster))

    # Stats
    action_counts = Counter(p["action"] for p in pairs)
    cross_source = sum(1 for p in pairs if p["source_a"] != p["source_b"])

    stats = {
        "total_pairs": len(pairs),
        "clusters": len(clusters),
        "action_counts": dict(action_counts),
        "cross_source_pairs": cross_source,
        "same_source_pairs": len(pairs) - cross_source,
        "threshold": threshold,
        "chunks_indexed": len(minhashes),
        "chunks_skipped": skipped,
    }

    return {
        "stats": stats,
        "pairs": pairs,
        "clusters": clusters,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate_report(
    input_path: Path | None = None,
    output_dir: Path | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    dry_run: bool = False,
) -> dict:
    """Generate deduplication report."""
    input_path = input_path or (PROJECT_ROOT / "output" / "corpus" / "merged_corpus.jsonl")
    output_dir = output_dir or input_path.parent

    if not input_path.exists():
        print(f"Error: {input_path} not found.")
        return {}

    chunks = []
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))

    print(f"Loaded {len(chunks)} chunks from {input_path}")

    report = analyze_duplicates(chunks, threshold)
    stats = report["stats"]

    print(f"\nDeduplication Report:")
    print(f"  Total pairs found: {stats['total_pairs']}")
    print(f"  Clusters: {stats['clusters']}")
    print(f"  Actions: {stats['action_counts']}")
    print(f"  Cross-source pairs: {stats['cross_source_pairs']}")
    print(f"  Same-source pairs: {stats['same_source_pairs']}")

    if dry_run:
        print(f"\nTop 10 pairs:")
        for p in report["pairs"][:10]:
            print(f"  {p['similarity']:.3f}  {p['chunk_a']} <-> {p['chunk_b']}  [{p['action']}]")
        return report

    # Write report
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "dedup_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, sort_keys=True)
    print(f"\n  Report written to: {report_path}")

    # Write actionable summary (just the DROP/MERGE recommendations)
    actionable = [p for p in report["pairs"] if p["action"] in ("DROP", "MERGE")]
    if actionable:
        action_path = output_dir / "dedup_actions.json"
        with open(action_path, "w", encoding="utf-8") as f:
            json.dump(actionable, f, indent=2, ensure_ascii=False, sort_keys=True)
        print(f"  Actionable items: {action_path} ({len(actionable)} pairs)")

    return report


def main():
    parser = argparse.ArgumentParser(description="Generate deduplication report for VEX corpus")
    parser.add_argument("--input", type=Path, help="Input JSONL corpus")
    parser.add_argument("--output", type=Path, help="Output directory")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help=f"Similarity threshold (default: {DEFAULT_THRESHOLD})")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    args = parser.parse_args()

    generate_report(
        input_path=args.input,
        output_dir=args.output,
        threshold=args.threshold,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
