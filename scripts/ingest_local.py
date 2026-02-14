"""Ingest VEX knowledge from local markdown files.

Reads markdown files from a configurable directory, splits at heading
boundaries, extracts VEX code blocks, and outputs ChunkV2 JSONL.

First target: vex-pattern-library skill files.

Usage:
    python scripts/ingest_local.py /path/to/markdown/dir
    python scripts/ingest_local.py /path/to/dir --source-id my-source --authority 0.8
    python scripts/ingest_local.py /path/to/dir --dry-run
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.schema import (
    PIPELINE_VERSION,
    ChunkV2,
    CodeBlock,
    ContentType,
    Difficulty,
    VEXContext,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OUTPUT_DIR = PROJECT_ROOT / "output" / "local"

# Heading pattern for markdown
_HEADING_RE = re.compile(r'^(#{1,4})\s+(.+)$', re.MULTILINE)

# Fenced code block pattern
_CODE_BLOCK_RE = re.compile(
    r'^```(\w*)\n(.*?)^```',
    re.MULTILINE | re.DOTALL,
)

# VEX code indicators
_VEX_INDICATORS = re.compile(
    r'@\w+|ch[fivs]?\(|point\(|prim\(|set\w+attrib\(|'
    r'pcopen\(|pcfind\(|nearpoints?\(|addpoint\(|addprim\(|'
    r'\bvector\b|\bfloat\b|\bint\b|\bforeach\b'
)

# Languages that are definitely VEX or VEX-like
_VEX_LANGUAGES = {"vex", "vfl", "c", "cpp", "c++", "hlsl", ""}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _is_vex_code(code: str, lang: str) -> bool:
    """Determine if a code block is VEX."""
    if lang.lower() in ("python", "py", "bash", "sh", "shell", "json", "yaml", "hscript"):
        return False
    if lang.lower() in _VEX_LANGUAGES:
        if _VEX_INDICATORS.search(code):
            return True
        # Unfenced code with no indicators -- only include if explicitly tagged
        if lang.lower() in ("vex", "vfl"):
            return True
    return False


def _estimate_difficulty_from_content(text: str, code: str) -> str:
    """Rule-based difficulty estimation."""
    combined = text + " " + code
    advanced_signals = [
        "pcopen", "pcfind", "pcfilter", "matrix", "quaternion", "dihedral",
        "solver", "intrinsic", "setprimintrinsic", "compile", "thread",
        "getbbox_size", "volumesample", "volumegradient",
    ]
    intermediate_signals = [
        "foreach", "for(", "while(", "ramp", "chramp", "noise", "curlnoise",
        "addpoint", "addprim", "removeprim", "nearpoint", "pcclose",
    ]

    if any(s in combined.lower() for s in advanced_signals):
        return Difficulty.ADVANCED.value
    if any(s in combined.lower() for s in intermediate_signals):
        return Difficulty.INTERMEDIATE.value
    return Difficulty.BEGINNER.value


def _detect_vex_context(code: str) -> list[str]:
    """Detect VEX execution context from code content."""
    contexts = set()

    if any(kw in code for kw in ["@Frame", "@Time", "@TimeInc", "dopfield", "gas_"]):
        contexts.add(VEXContext.DOP.value)
    if any(kw in code for kw in ["@voxelsize", "volumesample", "volumegradient"]):
        contexts.add(VEXContext.DOP.value)
    if any(kw in code for kw in ["@uv", "texture(", "colormap("]):
        contexts.add(VEXContext.MATERIAL.value)
    if any(kw in code for kw in ["@Channel", "chinput("]):
        contexts.add(VEXContext.CHOP.value)

    if not contexts:
        contexts.add(VEXContext.SOP.value)

    return sorted(contexts)


def parse_markdown_file(
    file_path: Path,
    source_id: str,
    source_authority: float,
) -> list[ChunkV2]:
    """Parse a markdown file into chunks split at headings."""
    content = file_path.read_text(encoding="utf-8", errors="replace")
    filename = file_path.stem

    # Split content at headings
    sections = []
    last_end = 0
    headings = list(_HEADING_RE.finditer(content))

    if not headings:
        # No headings -- treat entire file as one chunk
        sections.append(("", filename, content))
    else:
        # Content before first heading
        preamble = content[:headings[0].start()].strip()
        if preamble:
            sections.append(("", filename, preamble))

        for i, match in enumerate(headings):
            heading_text = match.group(2).strip()
            start = match.end()
            end = headings[i + 1].start() if i + 1 < len(headings) else len(content)
            section_content = content[start:end].strip()
            if section_content:
                sections.append((match.group(1), heading_text, section_content))

    chunks = []
    for seq, (level, heading, section_text) in enumerate(sections, 1):
        # Extract code blocks
        code_blocks = []
        prose_parts = []
        last_code_end = 0

        for m in _CODE_BLOCK_RE.finditer(section_text):
            lang = m.group(1)
            code = m.group(2).strip()

            # Add prose before this code block
            prose_before = section_text[last_code_end:m.start()].strip()
            if prose_before:
                prose_parts.append(prose_before)
            last_code_end = m.end()

            if code and _is_vex_code(code, lang):
                code_blocks.append(CodeBlock(
                    code=code,
                    line_context=f"{file_path.name}:{heading}",
                    is_complete=not code.rstrip().endswith("..."),
                ))

        # Add remaining prose
        remaining = section_text[last_code_end:].strip()
        if remaining:
            # Strip out any remaining code fence artifacts
            remaining = _CODE_BLOCK_RE.sub("", remaining).strip()
            if remaining:
                prose_parts.append(remaining)

        prose = "\n\n".join(prose_parts)

        # Skip empty sections
        if not prose and not code_blocks:
            continue

        all_code = " ".join(cb.code for cb in code_blocks)
        difficulty = _estimate_difficulty_from_content(prose, all_code)
        vex_context = _detect_vex_context(all_code)

        chunk_id = f"local_{source_id}_{filename.lower()}_{seq:03d}"

        chunk = ChunkV2(
            id=chunk_id,
            content=prose,
            code_blocks=code_blocks,
            content_type=ContentType.PATTERN.value if code_blocks else ContentType.CONCEPT.value,
            difficulty=difficulty,
            vex_context=vex_context,
            source_id=source_id,
            source_url=str(file_path),
            source_authority=source_authority,
            title=heading or filename,
            section=filename,
            pipeline_version=PIPELINE_VERSION,
        )
        chunks.append(chunk)

    return chunks


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def ingest_directory(
    input_dir: Path,
    source_id: str = "local-skill",
    source_authority: float = 0.85,
    dry_run: bool = False,
    output_dir: Path | None = None,
) -> list[ChunkV2]:
    """Ingest all markdown files from a directory."""
    output_dir = output_dir or OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    md_files = sorted(input_dir.rglob("*.md"))
    # Filter out obvious non-content files
    md_files = [f for f in md_files if f.name not in ("CLAUDE.md", "LICENSE.md", "CHANGELOG.md")]

    print(f"Found {len(md_files)} markdown files in {input_dir}")

    if dry_run:
        for f in md_files:
            print(f"  {f.relative_to(input_dir)}")
        return []

    all_chunks = []
    for fpath in md_files:
        rel = fpath.relative_to(input_dir)
        chunks = parse_markdown_file(fpath, source_id, source_authority)
        code_count = sum(len(c.code_blocks) for c in chunks)
        print(f"  {rel}: {len(chunks)} chunks, {code_count} code blocks")
        all_chunks.extend(chunks)

    if not all_chunks:
        print("No chunks produced.")
        return []

    # Write output
    outfile = output_dir / f"{source_id}.jsonl"
    with open(outfile, "w", encoding="utf-8") as f:
        for chunk in all_chunks:
            f.write(json.dumps(chunk.to_dict(), sort_keys=True, ensure_ascii=False) + "\n")

    print(f"\nTotal: {len(all_chunks)} chunks")
    print(f"Output: {outfile}")

    return all_chunks


def main():
    parser = argparse.ArgumentParser(description="Ingest VEX knowledge from local markdown files")
    parser.add_argument("input_dir", type=Path, help="Directory containing markdown files")
    parser.add_argument("--source-id", default="local-skill", help="Source ID for these chunks")
    parser.add_argument("--authority", type=float, default=0.85, help="Source authority (0-1)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path, help="Output directory")
    args = parser.parse_args()

    if not args.input_dir.is_dir():
        print(f"Error: {args.input_dir} is not a directory")
        sys.exit(1)

    ingest_directory(
        input_dir=args.input_dir,
        source_id=args.source_id,
        source_authority=args.authority,
        dry_run=args.dry_run,
        output_dir=args.output,
    )


if __name__ == "__main__":
    main()
