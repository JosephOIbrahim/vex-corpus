"""Rule-based auto-tagger for VEX corpus chunks.

Fills in missing metadata using pattern matching:
  - content_type: concept / pattern / reference / troubleshooting / discussion
  - difficulty: beginner / intermediate / advanced / expert
  - vex_context: sop / dop / cop / chop / cvex / material / solver
  - houdini_version_min: minimum Houdini version

Never overwrites existing tags -- only fills gaps.

Usage:
    python scripts/enrichment/auto_tagger.py
    python scripts/enrichment/auto_tagger.py --input output/corpus/merged_corpus.jsonl
    python scripts/enrichment/auto_tagger.py --dry-run
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.schema import ContentType, Difficulty, VEXContext

# ---------------------------------------------------------------------------
# Content type detection
# ---------------------------------------------------------------------------

_PATTERN_SIGNALS = [
    r'\bstep\s*\d+\b', r'\bfirst\b.*\bthen\b', r'\bhow\s+to\b',
    r'\brecipe\b', r'\bworkflow\b', r'\bsetup\b',
    r'\bpattern\b', r'\btechnique\b', r'\btrick\b',
]

_REFERENCE_SIGNALS = [
    r'\bsignature\b', r'\breturn(?:s|ed)?\s+(?:type|value)\b',
    r'\bparameter(?:s)?\b.*\btype\b', r'\bsyntax\b',
    r'^\w+\s*\(.*\)\s*$',  # function signature line
]

_TROUBLESHOOTING_SIGNALS = [
    r'\berror\b', r'\bfix(?:ed|ing)?\b', r'\bbug\b',
    r'\bwhy\s+does\b', r'\bwhy\s+is\b', r'\bwhy\s+do\b',
    r'\bdoesn.t\s+work\b', r'\bnot\s+working\b',
    r'\bproblem\b', r'\bissue\b', r'\btroubleshoot\b',
    r'\bslow\b.*\bperformance\b', r'\bperformance\b.*\bslow\b',
    r'\bcook\s+time\b',
]

_CONCEPT_SIGNALS = [
    r'\bwhat\s+is\b', r'\bintroduction\b', r'\boverview\b',
    r'\bexplain\b', r'\bunderstand\b', r'\bbasic(?:s|ally)?\b',
    r'\bconcept\b', r'\btheory\b',
]

_DISCUSSION_SIGNALS = [
    r'\bopinion\b', r'\bpersonally\b', r'\bi\s+think\b',
    r'\bforum\b', r'\bquestion\b.*\banswer\b',
    r'\bpros?\b.*\bcons?\b', r'\btrade.?off\b',
]


def detect_content_type(chunk: dict) -> str | None:
    """Detect content type from chunk text and code.

    Returns the detected content_type or None if uncertain.
    """
    text = (chunk.get("content", "") + " " + chunk.get("title", "")).lower()

    # Reference: chunks with function signatures or parameter docs
    if chunk.get("source_id") == "sidefx-vex-reference":
        return ContentType.REFERENCE.value
    for pat in _REFERENCE_SIGNALS:
        if re.search(pat, text):
            return ContentType.REFERENCE.value

    # Troubleshooting
    score = sum(1 for pat in _TROUBLESHOOTING_SIGNALS if re.search(pat, text))
    if score >= 2:
        return ContentType.TROUBLESHOOTING.value

    # Pattern: has code blocks and step-by-step or how-to structure
    has_code = bool(chunk.get("code_blocks")) or bool(chunk.get("code"))
    pattern_score = sum(1 for pat in _PATTERN_SIGNALS if re.search(pat, text))
    if has_code and pattern_score >= 1:
        return ContentType.PATTERN.value

    # Discussion
    score = sum(1 for pat in _DISCUSSION_SIGNALS if re.search(pat, text))
    if score >= 2:
        return ContentType.DISCUSSION.value

    # Concept (default for explanatory content)
    concept_score = sum(1 for pat in _CONCEPT_SIGNALS if re.search(pat, text))
    if concept_score >= 1:
        return ContentType.CONCEPT.value

    # Default: concept for chunks with explanation, pattern for code-heavy
    if has_code and len(chunk.get("content", "")) < 100:
        return ContentType.PATTERN.value
    return ContentType.CONCEPT.value


# ---------------------------------------------------------------------------
# Difficulty detection
# ---------------------------------------------------------------------------

_EXPERT_SIGNALS = [
    "solver", "compile", "opencl", "thread", "parallel",
    "gas_", "microsolver", "dopfield",
    "cvex_bsdf", "bsdf", "renderstate",
    "agentrig", "agentclip",
]

_ADVANCED_SIGNALS = [
    "pcopen", "pcfind", "pcfilter", "pcclose", "pcimport", "pcexport",
    "matrix", "matrix3", "quaternion", "dihedral", "qconvert",
    "intrinsic", "setprimintrinsic", "primintrinsic",
    "getbbox_size", "getbbox_center",
    "volumesample", "volumegradient", "volumeres",
    "foreach", "while(", "for(int",
    "addpoint", "addprim", "removeprim", "removepoint",
    "primuv", "xyzdist", "uvsample",
    "cracktransform", "maketransform", "instance",
    "nearpoints", "neighbourcount",
    "curlnoise", "flownoise",
]

_INTERMEDIATE_SIGNALS = [
    "ch(", "chf(", "chi(", "chv(", "chramp(",
    "ramp", "noise", "rand(", "random(",
    "fit(", "fit01(", "clamp(",
    "nearpoint(", "neighbour(",
    "length(", "normalize(", "dot(", "cross(",
    "lerp(", "smooth(",
    "set(", "vector(",
    "point(", "prim(", "detail(",
    "setpointattrib(", "setprimattrib(",
]

_BEGINNER_SIGNALS = [
    "@P", "@Cd", "@N", "@ptnum", "@numpt",
    "@pscale", "@id", "@age",
]


def detect_difficulty(chunk: dict) -> str | None:
    """Detect difficulty level from code and content.

    Returns the detected difficulty or None if uncertain.
    """
    # Gather all code text
    code_parts = []
    for cb in chunk.get("code_blocks", []):
        if isinstance(cb, dict):
            code_parts.append(cb.get("code", ""))
        else:
            code_parts.append(str(cb))
    if chunk.get("code"):
        code_parts.append(chunk["code"])
    code = " ".join(code_parts)

    text = chunk.get("content", "") + " " + chunk.get("title", "")
    combined = (code + " " + text).lower()

    # Score each level
    expert_score = sum(1 for s in _EXPERT_SIGNALS if s in combined)
    advanced_score = sum(1 for s in _ADVANCED_SIGNALS if s in combined)
    intermediate_score = sum(1 for s in _INTERMEDIATE_SIGNALS if s in combined)
    beginner_score = sum(1 for s in _BEGINNER_SIGNALS if s.lower() in combined)

    # Expert: any expert signal dominates
    if expert_score >= 1:
        return Difficulty.EXPERT.value

    # Advanced: multiple advanced signals
    if advanced_score >= 2:
        return Difficulty.ADVANCED.value

    # Intermediate: multiple intermediate signals or 1 advanced
    if intermediate_score >= 2 or advanced_score >= 1:
        return Difficulty.INTERMEDIATE.value

    # Code complexity heuristic
    if code:
        lines = code.strip().count("\n") + 1
        if lines > 20:
            return Difficulty.ADVANCED.value
        if lines > 8:
            return Difficulty.INTERMEDIATE.value

    # Beginner: only basic attribute access
    if beginner_score >= 1 and advanced_score == 0 and intermediate_score == 0:
        return Difficulty.BEGINNER.value

    return Difficulty.BEGINNER.value


# ---------------------------------------------------------------------------
# VEX context detection
# ---------------------------------------------------------------------------

_CONTEXT_PATTERNS = {
    VEXContext.DOP.value: [
        "@Frame", "@Time", "@TimeInc", "@Timescale",
        "dopfield", "gas_", "microsolver", "solver",
        "pop", "@age", "@life", "@dead",
        "fluidobject", "emitter",
    ],
    VEXContext.COP.value: [
        "@IX", "@IY", "@IR", "@IG", "@IB", "@IA",
        "cinput", "copinput", "colormap(",
    ],
    VEXContext.CHOP.value: [
        "@Channel",
        "chinput(", "chopinput(",
        "sample_rate",
    ],
    VEXContext.CVEX.value: [
        "cvex_bsdf", "bsdf(",
        "renderstate(", "rayhittest(",
        "gather(", "trace(",
    ],
    VEXContext.MATERIAL.value: [
        "surface(", "displacement(",
        "texture(", "environment(",
        "@uv", "teximport(",
        "diffuse(", "specular(",
        "computelighting(",
    ],
    VEXContext.SOLVER.value: [
        "solver", "prev_frame",
        "solverresult",
    ],
}


def detect_vex_context(chunk: dict) -> list[str] | None:
    """Detect VEX execution context from code content.

    Returns list of detected contexts or None if only SOP detected.
    """
    code_parts = []
    for cb in chunk.get("code_blocks", []):
        if isinstance(cb, dict):
            code_parts.append(cb.get("code", ""))
        else:
            code_parts.append(str(cb))
    if chunk.get("code"):
        code_parts.append(chunk["code"])
    combined = " ".join(code_parts)

    contexts = set()
    for ctx, signals in _CONTEXT_PATTERNS.items():
        for signal in signals:
            if signal in combined:
                contexts.add(ctx)
                break

    if not contexts:
        return None  # Default SOP, no change needed

    # Always include SOP unless it's clearly another context only
    if len(contexts) == 1 and VEXContext.SOP.value not in contexts:
        # If it has @P, @Cd, etc., it's also SOP
        sop_signals = ["@P", "@Cd", "@N", "@ptnum", "point(", "prim(", "npoints"]
        if any(s in combined for s in sop_signals):
            contexts.add(VEXContext.SOP.value)

    return sorted(contexts) if contexts else None


# ---------------------------------------------------------------------------
# Houdini version detection
# ---------------------------------------------------------------------------

_VERSION_PATTERNS = [
    (re.compile(r'@opinput\d+_'), "16.0", "Uses @opinputN_ syntax (deprecated in H17+)"),
    (re.compile(r'\bchramp\b'), "15.0", "chramp() available since H15"),
    (re.compile(r'\bpcfind_radius\b'), "17.0", "pcfind_radius() added in H17"),
    (re.compile(r'\bsetprimintrinsic\b'), "15.0", "setprimintrinsic() available since H15"),
    (re.compile(r'\bdetailintrinsic\b'), "15.5", "detailintrinsic() available since H15.5"),
    (re.compile(r'\binpointgroup\b'), "14.0", "inpointgroup() has been available since early versions"),
    (re.compile(r'\bdict\b'), "18.0", "VEX dict type added in H18"),
    (re.compile(r'\bforeach\s*\(\s*int\b'), "16.5", "Typed foreach added in H16.5"),
    (re.compile(r'\binstance\b'), "19.0", "instance() intrinsic workflow common in H19+"),
    (re.compile(r'\bnearpoints\b'), "15.0", "nearpoints() available since H15"),
    (re.compile(r'\busd_'), "19.0", "USD VEX functions added in H19+"),
    (re.compile(r'\bosd_limitSurface\b'), "21.0", "osd_limitSurface() added in H21"),
]


def detect_houdini_version(chunk: dict) -> tuple[str, str]:
    """Detect minimum Houdini version and notes.

    Returns (version_min, version_notes) tuple.
    """
    code_parts = []
    for cb in chunk.get("code_blocks", []):
        if isinstance(cb, dict):
            code_parts.append(cb.get("code", ""))
        else:
            code_parts.append(str(cb))
    if chunk.get("code"):
        code_parts.append(chunk["code"])
    combined = " ".join(code_parts)

    max_version = ""
    notes = []

    for pattern, version, note in _VERSION_PATTERNS:
        if pattern.search(combined):
            if not max_version or version > max_version:
                max_version = version
            notes.append(note)

    return max_version, "; ".join(notes) if notes else ""


# ---------------------------------------------------------------------------
# Chunk tagging
# ---------------------------------------------------------------------------

def tag_chunk(chunk: dict) -> tuple[dict, list[str]]:
    """Apply rule-based tags to a chunk. Never overwrites existing tags.

    Returns (modified_chunk, list_of_decisions_made).
    """
    decisions = []

    # Content type
    current_ct = chunk.get("content_type", "")
    if not current_ct or current_ct == ContentType.CONCEPT.value:
        detected = detect_content_type(chunk)
        if detected and detected != current_ct:
            chunk["content_type"] = detected
            decisions.append(f"content_type: {current_ct or 'empty'} -> {detected}")

    # Difficulty
    current_diff = chunk.get("difficulty", "")
    if not current_diff or current_diff == Difficulty.BEGINNER.value:
        detected = detect_difficulty(chunk)
        if detected and detected != current_diff:
            chunk["difficulty"] = detected
            decisions.append(f"difficulty: {current_diff or 'empty'} -> {detected}")

    # VEX context
    current_ctx = chunk.get("vex_context", [])
    if not current_ctx or current_ctx == [VEXContext.SOP.value]:
        detected = detect_vex_context(chunk)
        if detected and detected != current_ctx:
            chunk["vex_context"] = detected
            decisions.append(f"vex_context: {current_ctx} -> {detected}")

    # Houdini version
    current_ver = chunk.get("houdini_version_min", "")
    if not current_ver:
        version, notes = detect_houdini_version(chunk)
        if version:
            chunk["houdini_version_min"] = version
            chunk["houdini_version_notes"] = notes
            decisions.append(f"houdini_version_min: {version}")

    return chunk, decisions


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def tag_corpus(
    input_path: Path | None = None,
    output_path: Path | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """Apply auto-tagging to all chunks in a corpus."""
    input_path = input_path or (PROJECT_ROOT / "output" / "corpus" / "merged_corpus.jsonl")
    output_path = output_path or input_path

    if not input_path.exists():
        print(f"Error: {input_path} not found. Run merge_sources.py first.")
        return []

    chunks = []
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))

    print(f"Loaded {len(chunks)} chunks from {input_path}")

    if dry_run:
        for chunk in chunks[:10]:
            _, decisions = tag_chunk(dict(chunk))
            cid = chunk.get("id", "?")
            if decisions:
                print(f"  {cid}:")
                for d in decisions:
                    print(f"    {d}")
            else:
                print(f"  {cid}: no changes")
        return []

    # Tag all chunks
    total_changes = Counter()
    all_decisions = []

    for chunk in chunks:
        _, decisions = tag_chunk(chunk)
        for d in decisions:
            field = d.split(":")[0]
            total_changes[field] += 1
        all_decisions.extend(decisions)

    print(f"\nTagging results:")
    for field, count in sorted(total_changes.items()):
        print(f"  {field}: {count} chunks updated")

    # Summary by content_type
    ct_counts = Counter(c.get("content_type", "unknown") for c in chunks)
    print(f"\nContent type distribution:")
    for ct, count in sorted(ct_counts.items()):
        print(f"  {ct}: {count}")

    diff_counts = Counter(c.get("difficulty", "unknown") for c in chunks)
    print(f"\nDifficulty distribution:")
    for d, count in sorted(diff_counts.items()):
        print(f"  {d}: {count}")

    ctx_counts = Counter()
    for c in chunks:
        for ctx in c.get("vex_context", ["sop"]):
            ctx_counts[ctx] += 1
    print(f"\nVEX context distribution:")
    for ctx, count in sorted(ctx_counts.items()):
        print(f"  {ctx}: {count}")

    ver_count = sum(1 for c in chunks if c.get("houdini_version_min"))
    print(f"\nChunks with version info: {ver_count}")

    # Write
    with open(output_path, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, sort_keys=True, ensure_ascii=False) + "\n")

    # Write decision log
    log_path = output_path.parent / "tagging_log.jsonl"
    with open(log_path, "w", encoding="utf-8") as f:
        for d in all_decisions:
            f.write(json.dumps({"decision": d}) + "\n")

    print(f"\n  Written to: {output_path}")
    print(f"  Decision log: {log_path}")

    return chunks


def main():
    parser = argparse.ArgumentParser(description="Auto-tag VEX corpus chunks")
    parser.add_argument("--input", type=Path, help="Input JSONL corpus")
    parser.add_argument("--output", type=Path, help="Output JSONL (default: overwrite input)")
    parser.add_argument("--dry-run", action="store_true", help="Preview tags on sample chunks")
    args = parser.parse_args()

    tag_corpus(input_path=args.input, output_path=args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
