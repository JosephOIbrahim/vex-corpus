#!/usr/bin/env python3
"""Sync vex-corpus chunks into Synapse RAG knowledge layer.

One-way pipeline: reads merged_corpus.jsonl, generates markdown reference
files and merges entries into Synapse's semantic_index.json and
agent_relevance_map.json. Does NOT modify any Synapse code.

Usage:
    python scripts/sync_to_synapse.py [--synapse PATH] [--dry-run] [--force]
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PREFIX = "vex_corpus_"
MIN_TOPIC_SIZE = 10          # topics with fewer chunks merge into misc
MAX_CODE_LINES = 8           # truncate code blocks in rendered markdown
MAX_KEYWORDS = 50            # cap keywords per semantic index entry
AGENT_NAME = "sop_agent"     # all VEX corpus topics route to sop_agent
MANIFEST_NAME = ".vex_corpus_manifest.json"

# Pretty names for topics
TOPIC_LABELS = {
    "math_operations": "Math Operations",
    "point_cloud_ops": "Point Cloud Operations",
    "channel_references": "Channel References",
    "geometry_creation": "Geometry Creation",
    "edge_topology": "Edge & Topology",
    "color_operations": "Color Operations",
    "flow_visualization": "Flow & Visualization",
    "attribute_operations": "Attribute Operations",
    "conditional_logic": "Conditional Logic",
    "noise_patterns": "Noise Patterns",
    "matrix_transforms": "Matrix Transforms",
    "field_analysis": "Field Analysis",
    "loop_patterns": "Loop Patterns",
}

DIFFICULTY_ORDER = ["beginner", "intermediate", "advanced", "expert"]

# Source IDs → human-readable names
SOURCE_LABELS = {
    "joy-of-vex-youtube": "joy-of-vex-youtube",
    "sidefx-vex-reference": "sidefx-vex-reference",
    "cgwiki-vex": "cgwiki-vex",
    "vex-corpus-blueprints": "vex-corpus-blueprints",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def corpus_hash(corpus_path: Path) -> str:
    """SHA-256 of the entire corpus file for incremental skip."""
    h = hashlib.sha256()
    with open(corpus_path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def load_corpus(corpus_path: Path) -> list[dict]:
    """Load and return all chunks from the JSONL corpus."""
    chunks = []
    with open(corpus_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks


def sort_key(chunk: dict) -> tuple:
    """Deterministic sort: difficulty → source → id."""
    diff = chunk.get("difficulty", "")
    try:
        diff_idx = DIFFICULTY_ORDER.index(diff)
    except ValueError:
        diff_idx = len(DIFFICULTY_ORDER)
    return (diff_idx, chunk.get("source_id", ""), chunk.get("id", ""))


def truncate_code(code: str, max_lines: int = MAX_CODE_LINES) -> str:
    """Truncate code to max_lines, appending '// ...' if truncated."""
    lines = code.rstrip("\n").split("\n")
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[:max_lines]) + "\n// ..."


def collect_keywords(chunks: list[dict]) -> list[str]:
    """Harvest keywords from corpus metadata, deduplicated and capped."""
    seen = set()
    keywords = []

    for chunk in chunks:
        for field in ("functions_referenced", "attributes_read",
                      "attributes_written", "alternative_prompts",
                      "vex_context"):
            for kw in chunk.get(field, []):
                kw_lower = kw.lower().strip()
                if kw_lower and kw_lower not in seen:
                    seen.add(kw_lower)
                    keywords.append(kw_lower)

    return sorted(keywords)[:MAX_KEYWORDS]


def group_by_topic(chunks: list[dict]) -> dict[str, list[dict]]:
    """Group enriched chunks by llm_topic. Unenriched chunks are skipped."""
    groups = defaultdict(list)
    for chunk in chunks:
        topic = chunk.get("llm_topic", "")
        if topic:
            groups[topic].append(chunk)
    return dict(groups)


def partition_topics(groups: dict[str, list[dict]]) -> tuple[dict, list[dict]]:
    """Split into major topics (>=MIN_TOPIC_SIZE) and misc chunks."""
    major = {}
    misc_chunks = []
    for topic, chunks in sorted(groups.items()):
        if len(chunks) >= MIN_TOPIC_SIZE:
            major[topic] = chunks
        else:
            misc_chunks.extend(chunks)
    return major, misc_chunks


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def render_topic_md(topic_key: str, label: str, chunks: list[dict]) -> str:
    """Render a single topic's markdown file matching Synapse conventions."""
    chunks = sorted(chunks, key=sort_key)

    # Collect sources
    sources = sorted({SOURCE_LABELS.get(c.get("source_id", ""), c.get("source_id", "unknown"))
                      for c in chunks})

    lines = []
    lines.append(f"# VEX Corpus: {label}")
    lines.append("")
    lines.append(f"> {len(chunks)} examples from vex-corpus. Sources: {', '.join(sources)}")
    lines.append("")

    # Group by difficulty
    by_diff = defaultdict(list)
    for chunk in chunks:
        diff = chunk.get("difficulty", "unknown")
        by_diff[diff].append(chunk)

    for diff in DIFFICULTY_ORDER + ["unknown"]:
        diff_chunks = by_diff.get(diff, [])
        if not diff_chunks:
            continue

        diff_label = diff.capitalize() if diff != "unknown" else "Uncategorized"
        lines.append(f"## {diff_label} ({len(diff_chunks)} examples)")
        lines.append("")

        for chunk in diff_chunks:
            title = chunk.get("title", chunk.get("id", "Untitled"))
            lines.append(f"### {title}")
            lines.append("")

            # Code blocks
            code_blocks = chunk.get("code_blocks", [])
            if code_blocks:
                code = code_blocks[0].get("code", "")
                if code.strip():
                    lines.append("```vex")
                    lines.append(truncate_code(code))
                    lines.append("```")
                    lines.append("")

            # Description (prefer content, fall back to explanation)
            desc = chunk.get("content", "") or chunk.get("explanation", "")
            if desc:
                # Keep it to first sentence or first 200 chars
                first_sentence = desc.split(". ")[0].rstrip(".")
                if len(first_sentence) > 200:
                    first_sentence = first_sentence[:197] + "..."
                lines.append(first_sentence + ".")
                lines.append("")

    return "\n".join(lines)


def render_misc_md(chunks: list[dict]) -> str:
    """Render the misc catch-all file for small topics."""
    chunks = sorted(chunks, key=sort_key)
    sources = sorted({SOURCE_LABELS.get(c.get("source_id", ""), c.get("source_id", "unknown"))
                      for c in chunks})

    # Group by original topic for sub-sections
    by_topic = defaultdict(list)
    for chunk in chunks:
        by_topic[chunk.get("llm_topic", "misc")].append(chunk)

    lines = []
    lines.append("# VEX Corpus: Miscellaneous Topics")
    lines.append("")
    lines.append(f"> {len(chunks)} examples from vex-corpus (small topics). Sources: {', '.join(sources)}")
    lines.append("")

    for topic_key in sorted(by_topic.keys()):
        topic_chunks = by_topic[topic_key]
        label = topic_key.replace("_", " ").title()
        lines.append(f"## {label} ({len(topic_chunks)} examples)")
        lines.append("")

        for chunk in sorted(topic_chunks, key=sort_key):
            title = chunk.get("title", chunk.get("id", "Untitled"))
            lines.append(f"### {title}")
            lines.append("")

            code_blocks = chunk.get("code_blocks", [])
            if code_blocks:
                code = code_blocks[0].get("code", "")
                if code.strip():
                    lines.append("```vex")
                    lines.append(truncate_code(code))
                    lines.append("```")
                    lines.append("")

            desc = chunk.get("content", "") or chunk.get("explanation", "")
            if desc:
                first_sentence = desc.split(". ")[0].rstrip(".")
                if len(first_sentence) > 200:
                    first_sentence = first_sentence[:197] + "..."
                lines.append(first_sentence + ".")
                lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Metadata merging
# ---------------------------------------------------------------------------

def build_semantic_entry(file_key: str, label: str, chunks: list[dict]) -> dict:
    """Build a semantic_index.json entry for one topic."""
    keywords = collect_keywords(chunks)
    sources = sorted({c.get("source_id", "") for c in chunks if c.get("source_id")})

    return {
        "summary": f"VEX Corpus: {label} ({len(chunks)} examples)",
        "description": (
            f"Labeled VEX examples for {label.lower()} from vex-corpus. "
            f"Sources: {', '.join(sources)}. "
            f"Covers {len(chunks)} code snippets with difficulty ratings and metadata."
        ),
        "keywords": keywords,
        "reference_file": file_key,
    }


def merge_semantic_index(index_path: Path, new_entries: dict[str, dict]) -> dict:
    """Load existing index, remove stale vex_corpus_* entries, add new ones."""
    if index_path.exists():
        with open(index_path, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {}

    # Remove stale vex_corpus_ entries
    stale = [k for k in data if k.startswith(PREFIX)]
    for k in stale:
        del data[k]

    # Add new entries
    data.update(new_entries)
    return data


def merge_relevance_map(map_path: Path, new_keys: list[str]) -> dict:
    """Load existing map, remove stale vex_corpus_* entries, add new ones."""
    if map_path.exists():
        with open(map_path, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {}

    # Remove stale
    stale = [k for k in data if k.startswith(PREFIX)]
    for k in stale:
        del data[k]

    # Add new — all VEX corpus topics route to sop_agent
    for key in new_keys:
        data[key] = AGENT_NAME

    return data


def atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically via temp file + rename."""
    content = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    # Write to temp file in same directory, then rename
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix=path.stem
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        # On Windows, can't rename over existing file — remove first
        if path.exists():
            path.unlink()
        os.rename(tmp_path, str(path))
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_sync(
    corpus_path: Path,
    synapse_root: Path,
    dry_run: bool = False,
    force: bool = False,
) -> None:
    """Execute the full sync pipeline."""

    ref_dir = synapse_root / "rag" / "skills" / "houdini21-reference"
    meta_dir = synapse_root / "rag" / "documentation" / "_metadata"
    manifest_path = ref_dir / MANIFEST_NAME

    # Validate paths
    if not corpus_path.exists():
        print(f"ERROR: Corpus not found: {corpus_path}", file=sys.stderr)
        sys.exit(1)
    if not ref_dir.exists():
        print(f"ERROR: Synapse reference dir not found: {ref_dir}", file=sys.stderr)
        sys.exit(1)
    if not meta_dir.exists():
        print(f"ERROR: Synapse metadata dir not found: {meta_dir}", file=sys.stderr)
        sys.exit(1)

    # Check for incremental skip
    current_hash = corpus_hash(corpus_path)
    if not force and manifest_path.exists():
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        if manifest.get("corpus_hash") == current_hash:
            print("Corpus unchanged since last sync. Use --force to override.")
            return

    # Load and group
    print(f"Loading corpus from {corpus_path}...")
    chunks = load_corpus(corpus_path)
    print(f"  {len(chunks)} total chunks loaded")

    groups = group_by_topic(chunks)
    print(f"  {sum(len(v) for v in groups.values())} enriched chunks across {len(groups)} topics")

    major_topics, misc_chunks = partition_topics(groups)
    print(f"  {len(major_topics)} major topics (>={MIN_TOPIC_SIZE} chunks)")
    print(f"  {len(misc_chunks)} chunks in misc ({len(groups) - len(major_topics)} small topics)")

    # Build file plan
    file_plan = {}  # file_key -> (label, markdown_content)
    semantic_entries = {}
    all_keys = []

    for topic_key in sorted(major_topics.keys()):
        topic_chunks = major_topics[topic_key]
        label = TOPIC_LABELS.get(topic_key, topic_key.replace("_", " ").title())
        file_key = PREFIX + topic_key

        md_content = render_topic_md(topic_key, label, topic_chunks)
        file_plan[file_key] = (label, md_content)
        semantic_entries[file_key] = build_semantic_entry(file_key, label, topic_chunks)
        all_keys.append(file_key)

    # Misc file
    if misc_chunks:
        misc_key = PREFIX + "misc"
        misc_md = render_misc_md(misc_chunks)
        file_plan[misc_key] = ("Miscellaneous Topics", misc_md)
        semantic_entries[misc_key] = build_semantic_entry(
            misc_key, "Miscellaneous Topics", misc_chunks
        )
        all_keys.append(misc_key)

    # Report plan
    print(f"\nFiles to write ({len(file_plan)}):")
    for key, (label, content) in sorted(file_plan.items()):
        line_count = content.count("\n")
        print(f"  {key}.md  ({line_count} lines) — {label}")

    print(f"\nSemantic index entries: {len(semantic_entries)}")
    print(f"Agent relevance entries: {len(all_keys)} -> {AGENT_NAME}")

    if dry_run:
        print("\n[DRY RUN] No files written.")
        return

    # Write markdown files
    print("\nWriting markdown files...")
    for key, (label, content) in sorted(file_plan.items()):
        md_path = ref_dir / f"{key}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  wrote {md_path.name}")

    # Clean up any stale vex_corpus_ files not in current plan
    for existing in ref_dir.glob(f"{PREFIX}*.md"):
        if existing.stem not in file_plan:
            existing.unlink()
            print(f"  removed stale {existing.name}")

    # Merge semantic index
    print("\nMerging semantic_index.json...")
    index_path = meta_dir / "semantic_index.json"
    merged_index = merge_semantic_index(index_path, semantic_entries)
    atomic_write_json(index_path, merged_index)
    print(f"  {len(merged_index)} total entries ({len(semantic_entries)} from vex-corpus)")

    # Merge agent relevance map
    print("Merging agent_relevance_map.json...")
    map_path = meta_dir / "agent_relevance_map.json"
    merged_map = merge_relevance_map(map_path, all_keys)
    atomic_write_json(map_path, merged_map)
    print(f"  {len(merged_map)} total entries ({len(all_keys)} from vex-corpus)")

    # Write manifest
    manifest_data = {
        "corpus_hash": current_hash,
        "corpus_path": str(corpus_path),
        "chunks_total": len(chunks),
        "chunks_enriched": sum(len(v) for v in groups.values()),
        "topics_major": len(major_topics),
        "topics_misc": len(groups) - len(major_topics),
        "files_written": sorted(f"{k}.md" for k in file_plan),
        "semantic_keys": sorted(all_keys),
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    print(f"\nManifest written to {manifest_path.name}")

    print("\nSync complete.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync vex-corpus into Synapse RAG knowledge layer."
    )
    parser.add_argument(
        "--synapse",
        type=Path,
        default=None,
        help="Path to Synapse repo root (default: auto-detect sibling)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be written without writing anything",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force sync even if corpus hasn't changed",
    )
    args = parser.parse_args()

    # Resolve paths
    script_dir = Path(__file__).resolve().parent
    corpus_root = script_dir.parent
    corpus_path = corpus_root / "output" / "corpus" / "merged_corpus.jsonl"

    if args.synapse:
        synapse_root = args.synapse.resolve()
    else:
        # Auto-detect: sibling directory
        synapse_root = corpus_root.parent / "Synapse"

    if not synapse_root.exists():
        print(f"ERROR: Synapse root not found: {synapse_root}", file=sys.stderr)
        print("Use --synapse PATH to specify the Synapse repo location.", file=sys.stderr)
        sys.exit(1)

    run_sync(corpus_path, synapse_root, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
