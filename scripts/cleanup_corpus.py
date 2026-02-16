"""One-time corpus cleanup: fix consistency issues found in audit.

Fixes:
1. Remove hallucinated pcopen/pcclose bugs from chunks without those functions
2. Re-classify misclassified point_cloud_ops chunks using code heuristics
3. Normalize topic names (underscore, flatten lists, fix compound strings)
4. Fix 4 empty-content blueprint chunks
5. Regenerate stats.json
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORPUS_PATH = PROJECT_ROOT / "output" / "corpus" / "merged_corpus.jsonl"
STATS_PATH = PROJECT_ROOT / "output" / "corpus" / "stats.json"

# VEX function -> topic mapping for reclassification
TOPIC_HEURISTICS = {
    "noise_patterns": ["noise", "onoise", "snoise", "anoise", "curlnoise", "pnoise"],
    "point_cloud_ops": ["pcopen", "pcfind", "pcclose", "pcfilter", "pcimport",
                        "pcgenerate", "nearpoints", "nearpoint", "pgfind"],
    "channel_references": ["ch(", "chf(", "chi(", "chv(", "chs(", "chramp("],
    "color_operations": ["@Cd", "hsvtorgb", "rgbtohsv", "luminance"],
    "matrix_transforms": ["maketransform", "invert(", "transpose(", "ident(",
                          "matrix3", "matrix4", "rotate(", "scale("],
    "quaternion_operations": ["quaternion(", "qrotate(", "slerp(", "eulertoquaternion",
                              "qconvert", "dihedral("],
    "geometry_creation": ["addpoint(", "addprim(", "addvertex(", "removeprim(",
                          "removepoint(", "setprimvertex("],
    "loop_patterns": ["for (", "for(", "foreach(", "while(", "while ("],
    "edge_topology": ["hedge_", "vertexpoint(", "vertexprim(", "pointvertex(",
                      "primvertex(", "vertexnext(", "vertexprev(", "neighbours(",
                      "polyneighbours("],
    "attribute_operations": ["setpointattrib(", "setprimattrib(", "setdetailattrib(",
                             "setvertexattrib(", "attrib(", "point(", "prim(", "detail("],
    "math_operations": ["fit(", "fit01(", "clamp(", "lerp(", "smooth(",
                        "abs(", "floor(", "ceil(", "pow(", "sqrt(", "sin(",
                        "cos(", "atan2(", "radians(", "degrees("],
    "conditional_logic": ["if (", "if(", "else", "ternary"],
    "simulation_setup": ["solver", "@Frame", "@Time", "@TimeInc"],
    "string_operations": ["sprintf(", "split(", "join(", "strlen(", "substr("],
    "flow_visualization": ["advect", "curl", "streamline"],
}

# Normalize topic name: spaces to underscores, lowercase
TOPIC_NORMALIZE = {
    "point cloud operations": "point_cloud_ops",
    "point cloud operations (pcopen, pcfind)": "point_cloud_ops",
    "point cloud operations and aggregation": "point_cloud_ops",
    "point cloud operations, optimization patterns": "point_cloud_ops",
    "flow visualization": "flow_visualization",
    "geometry creation": "geometry_creation",
    "geometry creation and intersection": "geometry_creation",
    "geometry creation and manipulation": "geometry_creation",
    "edge topology": "edge_topology",
    "edge topology, edge operations": "edge_topology",
    "attribute operations": "attribute_operations",
    "string operations": "string_operations",
    "array operations": "attribute_operations",
    "vector operations": "math_operations",
    "mesh operations": "geometry_creation",
    "mesh manipulation": "geometry_creation",
    "ray tracing": "geometry_creation",
    "scattered points on limit surface": "subdivision_surfaces",
}


def get_code(chunk: dict) -> str:
    parts = []
    for cb in chunk.get("code_blocks", []):
        if isinstance(cb, dict):
            parts.append(cb.get("code", ""))
        else:
            parts.append(str(cb))
    if chunk.get("code"):
        parts.append(chunk["code"])
    return "\n".join(parts)


def classify_by_code(code: str) -> str:
    """Classify topic from code using function heuristics."""
    if not code or len(code) < 10:
        return ""

    scores = Counter()
    for topic, indicators in TOPIC_HEURISTICS.items():
        for ind in indicators:
            if ind in code:
                scores[topic] += 1

    if not scores:
        # Fallback: check for common patterns
        if "@Cd" in code and ("@P" in code or "@N" in code):
            return "color_operations"
        if "@P" in code or "@N" in code:
            return "math_operations"
        return "math_operations"  # safe default for code chunks

    return scores.most_common(1)[0][0]


def normalize_topic(topic) -> str:
    """Normalize a topic value to a clean underscore string."""
    if isinstance(topic, list):
        # Take first element
        topic = topic[0] if topic else ""

    if not isinstance(topic, str):
        return ""

    topic = topic.strip()

    # Check direct normalization map
    if topic.lower() in TOPIC_NORMALIZE:
        return TOPIC_NORMALIZE[topic.lower()]
    if topic in TOPIC_NORMALIZE:
        return TOPIC_NORMALIZE[topic]

    # Handle comma-separated compound topics -> take first
    if "," in topic:
        first = topic.split(",")[0].strip()
        # Recursively normalize the first part
        return normalize_topic(first)

    # Replace spaces with underscores
    if " " in topic:
        normalized = topic.lower().replace(" ", "_")
        # Check if the underscore version is a known topic
        if normalized in {t for topics in TOPIC_HEURISTICS for t in [topics]}:
            return normalized
        # Try the normalize map
        if topic.lower() in TOPIC_NORMALIZE:
            return TOPIC_NORMALIZE[topic.lower()]
        return normalized

    return topic


def fix_hallucinated_bugs(chunk: dict, code: str) -> int:
    """Remove pcopen/pcclose bugs from chunks that don't use those functions."""
    bugs = chunk.get("llm_bugs", [])
    if not bugs:
        return 0

    pc_funcs = {"pcopen", "pcfind", "pcclose", "pcfilter", "pcimport", "pcgenerate", "nearpoints"}
    has_pc = any(f in code for f in pc_funcs)

    if has_pc:
        return 0  # Bugs might be legitimate

    original_count = len(bugs)
    cleaned = [b for b in bugs if not (
        isinstance(b, dict) and (
            "pcopen" in str(b.get("type", "")).lower() or
            "pcclose" in str(b.get("type", "")).lower() or
            "pcopen" in str(b.get("description", "")).lower() or
            "pcclose" in str(b.get("description", "")).lower() or
            "resource_leak" in str(b.get("type", "")).lower()
        )
    )]

    if len(cleaned) != original_count:
        chunk["llm_bugs"] = cleaned
        chunk["llm_code_quality"] = "clean" if not cleaned else chunk.get("llm_code_quality", "minor_issues")
        return original_count - len(cleaned)

    return 0


def main():
    if not CORPUS_PATH.exists():
        print(f"Error: {CORPUS_PATH} not found")
        sys.exit(1)

    chunks = []
    with open(CORPUS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))

    print(f"Loaded {len(chunks)} chunks")

    stats = {
        "bugs_removed": 0,
        "topics_reclassified": 0,
        "topics_normalized": 0,
        "lists_flattened": 0,
        "empty_content_fixed": 0,
        "partial_llm_fixed": 0,
    }

    for chunk in chunks:
        code = get_code(chunk)
        cid = chunk.get("id", "?")

        # --- Fix 1: Remove hallucinated bugs ---
        removed = fix_hallucinated_bugs(chunk, code)
        if removed:
            stats["bugs_removed"] += removed

        # --- Fix 2: Reclassify misclassified point_cloud_ops ---
        topic = chunk.get("llm_topic", "")
        if topic and code:
            # Normalize first (handles lists, spaces, compounds)
            original = topic
            normalized = normalize_topic(topic)

            if normalized != str(original):
                stats["topics_normalized"] += 1
                if isinstance(original, list):
                    stats["lists_flattened"] += 1

            # Check if point_cloud_ops is valid
            if normalized == "point_cloud_ops":
                pc_funcs = {"pcopen", "pcfind", "pcclose", "pcfilter", "pcimport",
                            "pcgenerate", "nearpoints", "nearpoint", "pgfind"}
                if not any(f in code for f in pc_funcs):
                    # Reclassify using heuristics
                    better = classify_by_code(code)
                    if better and better != "point_cloud_ops":
                        normalized = better
                        stats["topics_reclassified"] += 1

            chunk["llm_topic"] = normalized

        # Also normalize secondary topics
        secondary = chunk.get("llm_secondary_topics", [])
        if secondary:
            if isinstance(secondary, str):
                secondary = [s.strip() for s in secondary.split(",")]
            normalized_sec = []
            for s in secondary:
                ns = normalize_topic(s)
                if ns:
                    normalized_sec.append(ns)
            chunk["llm_secondary_topics"] = normalized_sec

        # --- Fix 3: Fix empty content blueprint chunks ---
        if "content" in chunk and not chunk["content"] and code:
            chunk["content"] = code[:200]
            stats["empty_content_fixed"] += 1

        # --- Fix 4: Fill partial LLM fields with defaults ---
        if chunk.get("llm_topic") and code:
            if "llm_bugs" not in chunk:
                chunk["llm_bugs"] = []
                chunk["llm_code_quality"] = "unknown"
                stats["partial_llm_fixed"] += 1
            if "llm_complexity" not in chunk:
                chunk["llm_complexity"] = "unknown"
                chunk["llm_bottlenecks"] = []
                stats["partial_llm_fixed"] += 1

    # --- Write cleaned corpus ---
    print(f"\nWriting cleaned corpus...")
    with open(CORPUS_PATH, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, sort_keys=True, ensure_ascii=False) + "\n")

    # --- Fix 5: Regenerate stats.json ---
    print("Regenerating stats.json...")
    by_source = Counter()
    by_content_type = Counter()
    by_difficulty = Counter()
    with_code = 0
    topic_dist = Counter()

    for c in chunks:
        by_source[c.get("source_id", "unknown")] += 1
        by_content_type[c.get("content_type", "unknown")] += 1
        by_difficulty[c.get("difficulty", "unknown")] += 1
        code = get_code(c)
        if code and len(code) >= 10:
            with_code += 1
        t = c.get("llm_topic", "")
        if t:
            topic_dist[t] += 1

    new_stats = {
        "total_chunks": len(chunks),
        "with_code": with_code,
        "by_source": dict(sorted(by_source.items())),
        "by_content_type": dict(sorted(by_content_type.items())),
        "by_difficulty": dict(sorted(by_difficulty.items())),
        "by_llm_topic": dict(topic_dist.most_common()),
        "llm_coverage": {
            "llm_topic": sum(1 for c in chunks if c.get("llm_topic")),
            "llm_bugs": sum(1 for c in chunks if "llm_bugs" in c),
            "llm_complexity": sum(1 for c in chunks if c.get("llm_complexity")),
            "prompt": sum(1 for c in chunks if c.get("prompt")),
            "explanation": sum(1 for c in chunks if c.get("explanation")),
        },
    }

    with open(STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(new_stats, f, indent=2, sort_keys=True, ensure_ascii=False)

    # --- Report ---
    print(f"\n{'='*50}")
    print(f"CLEANUP SUMMARY")
    print(f"{'='*50}")
    print(f"  Hallucinated bugs removed:  {stats['bugs_removed']}")
    print(f"  Topics reclassified:        {stats['topics_reclassified']}")
    print(f"  Topics normalized:          {stats['topics_normalized']}")
    print(f"  List topics flattened:       {stats['lists_flattened']}")
    print(f"  Empty content fixed:        {stats['empty_content_fixed']}")
    print(f"  Partial LLM fields filled:  {stats['partial_llm_fixed']}")
    print(f"  stats.json regenerated:     Yes")
    print(f"\nTop topics after cleanup:")
    for t, n in topic_dist.most_common(15):
        print(f"  {t}: {n}")


if __name__ == "__main__":
    main()
