"""VEX code extractor and validator.

Enriches corpus chunks with:
  - functions_referenced: VEX function names extracted via static analysis
  - validation_warnings: common VEX syntax issues

Can also cross-reference extracted functions against SideFX reference chunks
to link function usage to documentation.

Usage:
    python scripts/enrichment/vex_extractor.py
    python scripts/enrichment/vex_extractor.py --input output/corpus/merged_corpus.jsonl
    python scripts/enrichment/vex_extractor.py --dry-run
"""

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# VEX function extraction
# ---------------------------------------------------------------------------

# Pattern: word followed by ( but not preceded by @ or . (exclude attribute access)
# Excludes common C keywords that look like functions
_FUNC_CALL_RE = re.compile(r'(?<![.@#])\b([a-zA-Z_]\w*)\s*\(')

# Known VEX built-in functions (subset -- common ones for validation)
KNOWN_VEX_FUNCTIONS = {
    # Math
    "abs", "ceil", "floor", "round", "clamp", "fit", "fit01", "fit10", "fit11",
    "lerp", "slerp", "smooth", "efit", "min", "max", "pow", "sqrt", "log", "exp",
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "radians", "degrees",
    "sign", "frac", "trunc",
    # Vector
    "length", "normalize", "dot", "cross", "distance", "reflect",
    "set", "getcomp", "setcomp",
    # Noise
    "noise", "snoise", "onoise", "pnoise", "curlnoise", "curlnoise2d",
    "anoise", "xnoise", "flownoise", "random", "rand", "nrandom",
    # Point cloud
    "pcopen", "pcfind", "pcfind_radius", "pcclose", "pcfilter",
    "pciterate", "pcunshaded", "pcimport", "pcexport", "pcnumfound",
    "nearpoint", "nearpoints", "neighbour", "neighbours", "neighbourcount",
    # Geometry
    "point", "prim", "vertex", "detail",
    "setpointattrib", "setprimattrib", "setvertexattrib", "setdetailattrib",
    "addpoint", "addprim", "addvertex", "removeprim", "removepoint",
    "npoints", "nprimitives", "nvertices",
    "pointattrib", "primattrib", "vertexattrib", "detailattrib",
    "attribsize", "attribtype", "haspointattrib", "hasprimattrib",
    "hasvertexattrib", "hasdetailattrib",
    "primuv", "xyzdist",
    # Intrinsics
    "primintrinsic", "setprimintrinsic", "pointintrinsic",
    "detailintrinsic",
    # Geometry info
    "getbbox", "getbbox_size", "getbbox_center", "getbbox_min", "getbbox_max",
    "getpointbbox", "getpointbbox_size", "getpointbbox_center",
    # Transform / matrix
    "ident", "invert", "transpose", "determinant", "translate", "rotate", "scale",
    "maketransform", "cracktransform", "quaternion", "dihedral", "qconvert",
    "lookat", "instance", "orient",
    # String
    "sprintf", "printf", "concat", "strlen", "substr", "find", "replace",
    "split", "join", "strip", "lstrip", "rstrip", "upper", "lower",
    "itoa", "atoi", "atof",
    # Channel
    "ch", "chf", "chi", "chv", "chs", "chp", "chramp", "chrampf",
    # Array
    "len", "resize", "append", "pop", "push", "insert", "removevalue",
    "removeindex", "sort", "reverse", "reorder", "argsort", "array",
    "find", "foreach",
    # Volume
    "volumesample", "volumesamplev", "volumegradient",
    "volumeres", "volumeindex", "volumeindextopos", "volumepostoindex",
    # Groups
    "inpointgroup", "inprimgroup", "invertexgroup",
    "setpointgroup", "setprimgroup", "setvertexgroup",
    "npointsgroup", "nprimitivesgroup",
    # VEX utility
    "assert", "error", "warning", "printf",
    "getattrib", "setattrib",
    "import", "sample_direction_uniform", "sample_hemisphere",
    # Solver / simulation
    "dopfield", "solverresult",
    # Texture / shading
    "texture", "colormap",
    # Conversion
    "vector", "float", "int", "string", "matrix", "matrix3",
}

# C/VEX keywords that look like functions but aren't
_NON_FUNCTIONS = {
    "if", "else", "for", "while", "do", "return", "break", "continue",
    "switch", "case", "default", "struct", "typedef", "define", "include",
    "pragma", "foreach",  # foreach is a statement, not a function
}

# Deprecated VEX patterns
_DEPRECATED_PATTERNS = [
    (re.compile(r'@opinput(\d+)_'), "Deprecated @opinputN_ syntax (pre-H17). Use opinput() or input index."),
    (re.compile(r'\bgetglobalvariable\b'), "getglobalvariable() is deprecated."),
]


def extract_functions(code: str) -> list[str]:
    """Extract VEX function names from code via regex analysis.

    Returns a sorted list of unique function names found.
    """
    matches = _FUNC_CALL_RE.findall(code)
    functions = set()
    for name in matches:
        name_lower = name.lower()
        if name_lower in _NON_FUNCTIONS:
            continue
        # Type casts look like functions but aren't always meaningful
        if name_lower in ("vector", "float", "int", "string", "matrix", "matrix3"):
            # Only include if it's clearly a cast: type(value)
            functions.add(name_lower)
            continue
        if name in KNOWN_VEX_FUNCTIONS or name_lower in KNOWN_VEX_FUNCTIONS:
            functions.add(name)
        elif re.match(r'^[a-z]', name):
            # Lowercase names that look like functions -- include them
            # even if not in our known list (VEX has ~400+ functions)
            functions.add(name)
    return sorted(functions)


# ---------------------------------------------------------------------------
# VEX validation
# ---------------------------------------------------------------------------

def validate_vex(code: str) -> list[str]:
    """Check for common VEX syntax issues.

    Returns a list of warning strings. These are informational -- they
    don't prevent the chunk from being used.
    """
    warnings = []

    lines = code.strip().split("\n")

    # Check for missing semicolons on statement lines
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("#"):
            continue
        # Lines that should end with ; but don't
        if (stripped and
            not stripped.endswith(";") and
            not stripped.endswith("{") and
            not stripped.endswith("}") and
            not stripped.endswith(",") and
            not stripped.endswith("(") and
            not stripped.endswith("\\") and
            not stripped.startswith("//") and
            not stripped.startswith("#") and
            not stripped.startswith("/*") and
            not stripped.startswith("*") and
            not stripped.endswith("*/") and
            not stripped.endswith(":") and  # case labels
            "=" in stripped):
            # This line has an assignment but no semicolon
            warnings.append(f"Line {i+1}: possible missing semicolon: {stripped[:60]}")

    # Check for unclosed braces
    open_braces = code.count("{") - code.count("}")
    if open_braces > 0:
        warnings.append(f"Unclosed braces: {open_braces} more {{ than }}")
    elif open_braces < 0:
        warnings.append(f"Extra closing braces: {-open_braces} more }} than {{")

    # Check for unclosed parentheses
    open_parens = code.count("(") - code.count(")")
    if open_parens > 0:
        warnings.append(f"Unclosed parentheses: {open_parens} more ( than )")
    elif open_parens < 0:
        warnings.append(f"Extra closing parentheses: {-open_parens} more ) than (")

    # Check for deprecated patterns
    for pattern, message in _DEPRECATED_PATTERNS:
        if pattern.search(code):
            warnings.append(message)

    # Check for pcopen without pcclose
    if "pcopen" in code and "pcclose" not in code:
        warnings.append("pcopen() called without pcclose() -- potential resource leak")

    # Check for global variable direct assignment (common mistake)
    if re.search(r'@ptnum\s*=', code):
        warnings.append("Assigning to @ptnum -- this is a read-only attribute")
    if re.search(r'@numpt\s*=', code):
        warnings.append("Assigning to @numpt -- this is a read-only attribute")
    if re.search(r'@numprim\s*=', code):
        warnings.append("Assigning to @numprim -- this is a read-only attribute")

    return warnings


# ---------------------------------------------------------------------------
# Chunk enrichment
# ---------------------------------------------------------------------------

def enrich_chunk(chunk: dict) -> dict:
    """Enrich a single chunk with extracted functions and validation.

    Modifies the chunk dict in place and returns it.
    """
    all_functions = set()
    all_warnings = []

    # Extract from code_blocks (v2 format)
    for cb in chunk.get("code_blocks", []):
        code = cb.get("code", "") if isinstance(cb, dict) else str(cb)
        if code:
            funcs = extract_functions(code)
            all_functions.update(funcs)
            warns = validate_vex(code)
            all_warnings.extend(warns)

    # Also check v1 'code' field
    v1_code = chunk.get("code", "")
    if v1_code:
        funcs = extract_functions(v1_code)
        all_functions.update(funcs)
        warns = validate_vex(v1_code)
        all_warnings.extend(warns)

    # Update chunk -- only add, never overwrite existing data
    existing_funcs = set(chunk.get("functions_referenced", []))
    merged_funcs = sorted(existing_funcs | all_functions)
    chunk["functions_referenced"] = merged_funcs

    existing_warnings = chunk.get("validation_warnings", [])
    new_warnings = [w for w in all_warnings if w not in existing_warnings]
    chunk["validation_warnings"] = existing_warnings + new_warnings

    return chunk


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def enrich_corpus(
    input_path: Path | None = None,
    output_path: Path | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """Enrich all chunks in a corpus file."""
    input_path = input_path or (PROJECT_ROOT / "output" / "corpus" / "merged_corpus.jsonl")
    output_path = output_path or input_path  # overwrite in place by default

    if not input_path.exists():
        print(f"Error: {input_path} not found. Run merge_sources.py first.")
        return []

    # Load
    chunks = []
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))

    print(f"Loaded {len(chunks)} chunks from {input_path}")

    if dry_run:
        # Sample a few chunks
        for chunk in chunks[:5]:
            enriched = enrich_chunk(dict(chunk))  # copy
            funcs = enriched.get("functions_referenced", [])
            warns = enriched.get("validation_warnings", [])
            print(f"  {chunk.get('id','?')}: {len(funcs)} functions, {len(warns)} warnings")
            if funcs:
                print(f"    Functions: {', '.join(funcs[:10])}")
            if warns:
                for w in warns[:3]:
                    print(f"    Warning: {w}")
        return []

    # Enrich
    total_funcs_added = 0
    total_warnings_added = 0
    chunks_with_functions = 0

    for chunk in chunks:
        old_funcs = len(chunk.get("functions_referenced", []))
        old_warns = len(chunk.get("validation_warnings", []))

        enrich_chunk(chunk)

        new_funcs = len(chunk.get("functions_referenced", []))
        new_warns = len(chunk.get("validation_warnings", []))

        total_funcs_added += (new_funcs - old_funcs)
        total_warnings_added += (new_warns - old_warns)
        if new_funcs > 0:
            chunks_with_functions += 1

    # Collect unique functions across corpus
    all_unique_funcs = set()
    for chunk in chunks:
        all_unique_funcs.update(chunk.get("functions_referenced", []))

    print(f"\nEnrichment results:")
    print(f"  Functions added: {total_funcs_added}")
    print(f"  Chunks with functions: {chunks_with_functions}/{len(chunks)}")
    print(f"  Unique functions: {len(all_unique_funcs)}")
    print(f"  Validation warnings added: {total_warnings_added}")

    # Write
    with open(output_path, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, sort_keys=True, ensure_ascii=False) + "\n")

    print(f"  Written to: {output_path}")

    return chunks


def main():
    parser = argparse.ArgumentParser(description="Extract VEX functions and validate code in corpus")
    parser.add_argument("--input", type=Path, help="Input JSONL corpus")
    parser.add_argument("--output", type=Path, help="Output JSONL corpus (default: overwrite input)")
    parser.add_argument("--dry-run", action="store_true", help="Preview enrichment on sample chunks")
    args = parser.parse_args()

    enrich_corpus(
        input_path=args.input,
        output_path=args.output,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
