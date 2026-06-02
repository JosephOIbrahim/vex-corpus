#!/usr/bin/env python3
"""License-aware VEX harvester for open-source GitHub repos (Phase 1).

See ``docs/SAMPLE_STRATEGY.md`` sections 4 and 5.2. This implements the
"harvest where green-tier supply exists" half of the strategy. The
non-negotiable rule:

    No redistributable license -> we read NO code.

The harvester operates on a **local checkout** of a repo (clone it yourself),
so it has no network dependency and works regardless of the environment's
network policy. License/attribution/url come from ``config/sources.yaml`` and
are gated against ``schema.REDISTRIBUTABLE_LICENSES`` *before* any file is
parsed.

What it extracts:
  - ``.vfl`` / ``.vex``           -> the whole program as one chunk
  - ``.h`` (VEX include headers)  -> one chunk per top-level function def
  - ``.md``                       -> fenced ```vex / ```vfl code blocks

Every chunk is stamped with the source license + a commit-pinned permalink and
run through the quality gate (``scripts/quality/verify_vex.py``).

Usage:
    # 1. Clone the repo locally first (the harvester does not fetch)
    git clone https://github.com/thi-ng/vexed-generation /tmp/vgen

    # 2. Harvest it (source-id must exist in sources.yaml w/ a green license)
    python scripts/scrapers/harvest_github.py \\
        --repo-dir /tmp/vgen --source-id thi-ng-vexed-generation \\
        --commit $(git -C /tmp/vgen rev-parse HEAD) --domain procedural_modeling

    python scripts/scrapers/harvest_github.py --repo-dir /tmp/vgen \\
        --source-id thi-ng-vexed-generation --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.schema import (  # noqa: E402
    REDISTRIBUTABLE_LICENSES,
    ChunkV2,
    CodeBlock,
)
from scripts.quality.verify_vex import verify_code  # noqa: E402

SOURCES_YAML = PROJECT_ROOT / "config" / "sources.yaml"
DEFAULT_OUT_DIR = PROJECT_ROOT / "output" / "harvest"

VEX_FILE_SUFFIXES = {".vfl", ".vex", ".h"}


# ---------------------------------------------------------------------------
# Source metadata + license gate
# ---------------------------------------------------------------------------

@dataclass
class SourceMeta:
    id: str
    url: str
    license: str
    attribution: str
    authority: float
    vex_contexts: list[str]
    reference_only: bool = False


def load_source_meta(source_id: str,
                     sources_path: Path = SOURCES_YAML) -> SourceMeta:
    data = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    for s in data.get("sources", []):
        if s.get("id") == source_id:
            return SourceMeta(
                id=source_id,
                url=s.get("url", ""),
                license=s.get("license", ""),
                attribution=s.get("attribution", ""),
                authority=float(s.get("authority", 0.0)),
                vex_contexts=s.get("vex_contexts", ["sop"]),
                reference_only=bool(s.get("reference_only", False)),
            )
    raise KeyError(f"source id '{source_id}' not found in {sources_path}")


def assert_harvestable(meta: SourceMeta) -> None:
    """The hard wall: refuse anything not cleared for verbatim redistribution."""
    if meta.reference_only:
        raise PermissionError(
            f"source '{meta.id}' is reference_only -- code must NOT be stored "
            f"verbatim. Index facts/links only."
        )
    if meta.license not in REDISTRIBUTABLE_LICENSES:
        raise PermissionError(
            f"source '{meta.id}' has license '{meta.license or '(none)'}', which "
            f"is not redistributable. Confirm an SPDX license in "
            f"{REDISTRIBUTABLE_LICENSES} before harvesting code."
        )


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

@dataclass
class Extracted:
    code: str
    title: str
    doc: str          # leading comment / context, if any
    start_line: int   # 1-based line in the source file


_MD_BLOCK_RE = re.compile(r"```(vex|vfl)[ \t]*\n(.*?)```", re.DOTALL | re.IGNORECASE)

# A top-level function signature followed by an opening brace. Matches VEX
# helper-library style: "vector vgFoo(vector p; float s)" and "cvex surf(...)"
# and "function foo(...)". Note VEX separates parameters with ';', so the
# parameter list must allow semicolons (only braces are excluded).
_FUNC_SIG_RE = re.compile(
    r"(?:^|\n)[ \t]*"
    r"(?P<sig>(?:export[ \t]+)?[A-Za-z_][\w<>\[\]]*[ \t]+[A-Za-z_]\w*[ \t]*\([^{}]*\))"
    r"[ \t]*\{",
    re.MULTILINE,
)


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _leading_comment(text: str, sig_start: int) -> str:
    """Grab a contiguous // or /* */ comment block immediately above sig_start."""
    head = text[:sig_start].rstrip()
    lines = head.split("\n")
    collected: list[str] = []
    for line in reversed(lines):
        s = line.strip()
        if s.startswith("//"):
            collected.append(s.lstrip("/ ").rstrip())
        elif s.endswith("*/") or s.startswith("*") or s.startswith("/*"):
            collected.append(s.strip("/* ").rstrip())
        else:
            break
    return " ".join(reversed(collected)).strip()


def _match_brace_body(text: str, open_brace: int) -> int:
    """Return index just past the matching close brace for text[open_brace]=='{'."""
    depth = 0
    for i in range(open_brace, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1  # unbalanced


def extract_md_vex_blocks(text: str) -> list[Extracted]:
    """Fenced ```vex / ```vfl blocks, titled from the nearest preceding heading."""
    out: list[Extracted] = []
    for n, m in enumerate(_MD_BLOCK_RE.finditer(text), 1):
        code = m.group(2).strip()
        if not code:
            continue
        # nearest markdown heading above the block
        head = text[:m.start()]
        headings = re.findall(r"(?m)^#{1,6}[ \t]+(.+?)[ \t]*$", head)
        title = headings[-1].strip() if headings else f"snippet {n}"
        out.append(Extracted(code=code, title=title, doc="",
                             start_line=_line_of(text, m.start(2))))
    return out


def extract_vex_functions(text: str) -> list[Extracted]:
    """One Extracted per top-level function definition in a VEX header/file."""
    out: list[Extracted] = []
    for m in _FUNC_SIG_RE.finditer(text):
        open_brace = m.end() - 1
        end = _match_brace_body(text, open_brace)
        if end < 0:
            continue
        sig = m.group("sig").strip()
        body = text[m.start("sig"):end]
        name_m = re.search(r"([A-Za-z_]\w*)[ \t]*\(", sig)
        title = name_m.group(1) if name_m else "function"
        out.append(Extracted(
            code=body.strip(),
            title=title,
            doc=_leading_comment(text, m.start("sig")),
            start_line=_line_of(text, m.start("sig")),
        ))
    return out


def extract_from_file(path: Path, text: str) -> list[Extracted]:
    suffix = path.suffix.lower()
    if suffix == ".md":
        return extract_md_vex_blocks(text)
    if suffix in (".h",):
        return extract_vex_functions(text)
    if suffix in (".vfl", ".vex"):
        # whole-program file; treat as a single chunk
        code = text.strip()
        if not code:
            return []
        funcs = extract_vex_functions(text)
        # If the file is a library of functions, prefer per-function chunks.
        if len(funcs) >= 2:
            return funcs
        return [Extracted(code=code, title=path.stem, doc="", start_line=1)]
    return []


# ---------------------------------------------------------------------------
# Harvest
# ---------------------------------------------------------------------------

def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _pinned_url(meta: SourceMeta, relpath: str, start: int, n_lines: int,
                commit: str) -> str:
    base = meta.url.rstrip("/")
    if base.endswith(".git"):
        base = base[:-4]
    if not base:
        return ""
    anchor = f"#L{start}-L{start + max(n_lines - 1, 0)}" if start else ""
    return f"{base}/blob/{commit}/{relpath}{anchor}"


def harvest_repo(repo_dir: Path, meta: SourceMeta, *, commit: str = "main",
                 domain: str = "", static_only: bool = False) -> list[ChunkV2]:
    """Walk a local repo checkout and produce verified, license-stamped chunks."""
    assert_harvestable(meta)

    primary_ctx = meta.vex_contexts[0] if meta.vex_contexts else "sop"
    chunks: list[ChunkV2] = []
    seen_checksums: set[str] = set()
    seq = 0

    for path in sorted(repo_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (VEX_FILE_SUFFIXES | {".md"}):
            continue
        if any(part in {".git", "node_modules"} for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        relpath = path.relative_to(repo_dir).as_posix()
        for ex in extract_from_file(path, text):
            if not ex.code.strip():
                continue
            seq += 1
            res = verify_code(ex.code, primary_ctx, static_only=static_only)
            n_lines = ex.code.count("\n") + 1
            chunk = ChunkV2(
                id=f"{meta.id}_{_slug(relpath)}_{seq:03d}",
                content=ex.doc,
                code_blocks=[CodeBlock(code=ex.code, line_context=relpath,
                                       is_complete=res.passed)],
                content_type="pattern",
                difficulty="intermediate",
                vex_context=list(meta.vex_contexts),
                subcontext=path.suffix.lower().lstrip("."),
                domain=domain,
                source_id=meta.id,
                source_url=_pinned_url(meta, relpath, ex.start_line, n_lines, commit),
                source_authority=meta.authority,
                title=ex.title,
                section=relpath,
                license=meta.license,
                attribution=meta.attribution,
                prompt=ex.title,
                explanation=ex.doc,
                verified=res.verified,
                verification_method=res.method,
                verified_houdini_build=res.houdini_build,
                validation_warnings=res.warnings if res.passed else res.errors,
                flagged_for_review=not res.passed,
                review_reason="" if res.passed else "; ".join(res.errors),
            )
            if chunk.checksum in seen_checksums:
                continue
            seen_checksums.add(chunk.checksum)
            chunks.append(chunk)

    return chunks


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="License-aware GitHub VEX harvester")
    ap.add_argument("--repo-dir", type=Path, required=True,
                    help="local checkout of the repo to harvest")
    ap.add_argument("--source-id", required=True,
                    help="id in config/sources.yaml (must have a green license)")
    ap.add_argument("--commit", default="main",
                    help="commit SHA for pinned source URLs (default: main)")
    ap.add_argument("--domain", default="", help="Domain tag for the chunks")
    ap.add_argument("--static-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="report only; do not write output")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    if not args.repo_dir.is_dir():
        print(f"error: {args.repo_dir} is not a directory", file=sys.stderr)
        return 2

    try:
        meta = load_source_meta(args.source_id)
        assert_harvestable(meta)
    except (KeyError, PermissionError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 3

    chunks = harvest_repo(args.repo_dir, meta, commit=args.commit,
                          domain=args.domain, static_only=args.static_only)

    verified = sum(1 for c in chunks if c.verified)
    flagged = sum(1 for c in chunks if c.flagged_for_review)
    print(f"harvested {len(chunks)} chunks from {args.source_id} "
          f"(license {meta.license})")
    print(f"  verified: {verified}   flagged: {flagged}   "
          f"method: {chunks[0].verification_method if chunks else 'n/a'}")

    if args.dry_run:
        for c in chunks[:10]:
            print(f"  - {c.id}  [{c.title}]  {c.source_url}")
        if len(chunks) > 10:
            print(f"  ... and {len(chunks) - 10} more")
        return 0

    out_path = args.out or (DEFAULT_OUT_DIR / f"{args.source_id}.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")
    print(f"  wrote {out_path.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
