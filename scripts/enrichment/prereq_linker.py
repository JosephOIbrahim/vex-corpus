"""Prerequisites linker for VEX corpus chunks.

Builds a concept graph and links chunks to their prerequisites:
  - Sequential lessons: JoyOfVex day N requires day N-1
  - Function usage: chunks using advanced functions link to concept chunks
  - Cross-source: cgwiki chunks link to matching sidefx reference chunks

Outputs prereq_graph.json with the full dependency map.

Usage:
    python scripts/enrichment/prereq_linker.py
    python scripts/enrichment/prereq_linker.py --input output/corpus/merged_corpus.jsonl
    python scripts/enrichment/prereq_linker.py --dry-run
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Concept extraction
# ---------------------------------------------------------------------------

# Map VEX concepts to the chunk IDs that introduce them
CONCEPT_INTRO_PATTERNS = {
    "attributes": re.compile(r'\b(?:what\s+(?:is|are)\s+)?attributes?\b.*@', re.IGNORECASE),
    "vectors": re.compile(r'\bvectors?\b.*(?:xyz|component|direction)', re.IGNORECASE),
    "noise": re.compile(r'\bnoise\b.*(?:random|procedural|pattern)', re.IGNORECASE),
    "loops": re.compile(r'\b(?:for|while|foreach)\b.*\b(?:loop|iterate)', re.IGNORECASE),
    "point_clouds": re.compile(r'\bpoint\s*cloud\b|\bpcopen\b|\bpcfind\b', re.IGNORECASE),
    "matrices": re.compile(r'\bmatr(?:ix|ices)\b.*(?:transform|rotate|scale)', re.IGNORECASE),
    "solver": re.compile(r'\bsolver\b.*(?:sop|frame|prev)', re.IGNORECASE),
    "volumes": re.compile(r'\bvolume\b.*(?:voxel|sample|field)', re.IGNORECASE),
    "groups": re.compile(r'\bgroup\b.*(?:point|prim|vertex)', re.IGNORECASE),
    "intrinsics": re.compile(r'\bintrinsic\b.*(?:prim|bound|area)', re.IGNORECASE),
    "uv": re.compile(r'\buv\b.*(?:coord|map|unwrap|texture)', re.IGNORECASE),
    "ramps": re.compile(r'\bramp\b.*(?:color|float|gradient)', re.IGNORECASE),
    "channels": re.compile(r'\b(?:ch|chf|chi|chv)\b.*(?:param|slider|channel)', re.IGNORECASE),
}

# Function -> concept mapping (which concept does using this function require?)
FUNCTION_CONCEPT_MAP = {
    "pcopen": "point_clouds", "pcfind": "point_clouds", "pcfind_radius": "point_clouds",
    "pcfilter": "point_clouds", "pcclose": "point_clouds", "pcimport": "point_clouds",
    "nearpoint": "point_clouds", "nearpoints": "point_clouds",
    "neighbour": "point_clouds", "neighbours": "point_clouds",
    "noise": "noise", "snoise": "noise", "onoise": "noise",
    "curlnoise": "noise", "flownoise": "noise", "xnoise": "noise",
    "matrix": "matrices", "invert": "matrices", "transpose": "matrices",
    "cracktransform": "matrices", "maketransform": "matrices",
    "quaternion": "matrices", "dihedral": "matrices",
    "volumesample": "volumes", "volumesamplev": "volumes",
    "volumegradient": "volumes", "volumeres": "volumes",
    "chramp": "ramps", "chrampf": "ramps",
    "ch": "channels", "chf": "channels", "chi": "channels", "chv": "channels",
    "inpointgroup": "groups", "setpointgroup": "groups",
    "inprimgroup": "groups", "setprimgroup": "groups",
    "primintrinsic": "intrinsics", "setprimintrinsic": "intrinsics",
    "detailintrinsic": "intrinsics",
    "primuv": "uv", "uvsample": "uv",
}


def _extract_episode_number(chunk_id: str) -> int | None:
    """Extract episode number from Joy of VEX chunk IDs like joy_of_vex_ep05_001."""
    m = re.search(r'ep(\d+)', chunk_id)
    return int(m.group(1)) if m else None


def _find_concept_intros(chunks: list[dict]) -> dict[str, list[str]]:
    """Find which chunks introduce each concept.

    Returns {concept: [chunk_id, ...]} sorted by source authority.
    """
    concept_chunks = defaultdict(list)

    for chunk in chunks:
        text = chunk.get("content", "") + " " + chunk.get("title", "")
        cid = chunk.get("id", "")
        authority = chunk.get("source_authority", 0)

        for concept, pattern in CONCEPT_INTRO_PATTERNS.items():
            if pattern.search(text):
                concept_chunks[concept].append((cid, authority))

    # Sort by authority (highest first) and return just IDs
    result = {}
    for concept, items in concept_chunks.items():
        items.sort(key=lambda x: -x[1])
        result[concept] = [cid for cid, _ in items]

    return result


# ---------------------------------------------------------------------------
# Prerequisite linking
# ---------------------------------------------------------------------------

def build_prereq_graph(chunks: list[dict]) -> dict:
    """Build prerequisite graph across the corpus.

    Returns a dict with:
      - concept_intros: {concept: [chunk_ids]}
      - prerequisites: {chunk_id: [prereq_chunk_ids]}
      - stats: summary statistics
    """
    # Index chunks by ID
    by_id = {c.get("id", ""): c for c in chunks}

    # Index Joy of VEX episodes
    jov_episodes = defaultdict(list)
    for c in chunks:
        cid = c.get("id", "")
        ep = _extract_episode_number(cid)
        if ep is not None and c.get("source_id") in ("joy-of-vex-youtube", "joy_of_vex"):
            jov_episodes[ep].append(cid)

    # Find concept introductions
    concept_intros = _find_concept_intros(chunks)

    # Build prerequisites
    prerequisites = defaultdict(set)

    # Rule 1: Sequential Joy of VEX episodes
    for ep, chunk_ids in sorted(jov_episodes.items()):
        if ep > 1 and (ep - 1) in jov_episodes:
            prev_chunks = jov_episodes[ep - 1]
            # Link first chunk of this episode to first chunk of previous
            if chunk_ids and prev_chunks:
                prerequisites[chunk_ids[0]].add(prev_chunks[0])

    # Rule 2: Function usage -> concept introduction
    for chunk in chunks:
        cid = chunk.get("id", "")
        funcs = chunk.get("functions_referenced", [])

        for func in funcs:
            concept = FUNCTION_CONCEPT_MAP.get(func)
            if concept and concept in concept_intros:
                intro_ids = concept_intros[concept]
                for intro_id in intro_ids[:2]:  # Link to top 2 intros
                    if intro_id != cid:
                        prerequisites[cid].add(intro_id)

    # Rule 3: cgwiki chunks with matching Joy of VEX content
    # (cgwiki pages often reference specific JoV episodes)
    for chunk in chunks:
        cid = chunk.get("id", "")
        if chunk.get("source_id") != "cgwiki-vex":
            continue
        text = chunk.get("content", "") + " " + chunk.get("title", "")
        # Look for JoyOfVex references
        for m in re.finditer(r'(?:joy\s*of\s*vex|jov)\s*(\d+)', text, re.IGNORECASE):
            ep = int(m.group(1))
            if ep in jov_episodes:
                for jov_id in jov_episodes[ep][:1]:
                    prerequisites[cid].add(jov_id)

    # Convert sets to sorted lists
    prereqs = {k: sorted(v) for k, v in prerequisites.items() if v}

    # Stats
    chunks_with_prereqs = len(prereqs)
    total_links = sum(len(v) for v in prereqs.values())
    concepts_found = len(concept_intros)

    return {
        "concept_intros": {k: v[:5] for k, v in concept_intros.items()},  # Top 5 per concept
        "prerequisites": prereqs,
        "stats": {
            "chunks_with_prereqs": chunks_with_prereqs,
            "total_links": total_links,
            "concepts_found": concepts_found,
            "jov_episodes_linked": len(jov_episodes),
        },
    }


# ---------------------------------------------------------------------------
# Chunk enrichment
# ---------------------------------------------------------------------------

def enrich_with_prereqs(
    input_path: Path | None = None,
    output_path: Path | None = None,
    dry_run: bool = False,
) -> dict:
    """Build prereq graph and enrich chunks with prerequisites field."""
    input_path = input_path or (PROJECT_ROOT / "output" / "corpus" / "merged_corpus.jsonl")
    output_path = output_path or input_path

    if not input_path.exists():
        print(f"Error: {input_path} not found. Run merge_sources.py first.")
        return {}

    chunks = []
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))

    print(f"Loaded {len(chunks)} chunks from {input_path}")

    graph = build_prereq_graph(chunks)
    stats = graph["stats"]

    print(f"\nPrerequisite graph:")
    print(f"  Concepts found: {stats['concepts_found']}")
    print(f"  Chunks with prerequisites: {stats['chunks_with_prereqs']}")
    print(f"  Total links: {stats['total_links']}")
    print(f"  Joy of VEX episodes linked: {stats['jov_episodes_linked']}")

    if dry_run:
        print(f"\nConcept introductions:")
        for concept, ids in sorted(graph["concept_intros"].items()):
            print(f"  {concept}: {ids[:3]}")

        print(f"\nSample prerequisites:")
        for cid, prereqs in list(graph["prerequisites"].items())[:10]:
            print(f"  {cid} <- {prereqs}")
        return graph

    # Enrich chunks
    prereqs_map = graph["prerequisites"]
    enriched = 0
    for chunk in chunks:
        cid = chunk.get("id", "")
        if cid in prereqs_map:
            existing = set(chunk.get("prerequisites", []))
            new = set(prereqs_map[cid])
            merged = sorted(existing | new)
            if merged != chunk.get("prerequisites", []):
                chunk["prerequisites"] = merged
                enriched += 1

    print(f"\n  Enriched {enriched} chunks with prerequisites")

    # Write corpus
    with open(output_path, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"  Written to: {output_path}")

    # Write graph
    graph_path = output_path.parent / "prereq_graph.json"
    with open(graph_path, "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=2, ensure_ascii=False, sort_keys=True)
    print(f"  Graph written to: {graph_path}")

    return graph


def main():
    parser = argparse.ArgumentParser(description="Build prerequisite links for VEX corpus")
    parser.add_argument("--input", type=Path, help="Input JSONL corpus")
    parser.add_argument("--output", type=Path, help="Output JSONL (default: overwrite input)")
    parser.add_argument("--dry-run", action="store_true", help="Preview graph without writing")
    args = parser.parse_args()

    enrich_with_prereqs(input_path=args.input, output_path=args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
