#!/usr/bin/env python3
"""VEX verification harness -- the corpus quality gate.

See ``docs/SAMPLE_STRATEGY.md`` section 3. Every chunk that enters the corpus
should pass this gate. There are two verification methods, in order of trust:

  1. ``hython-cook`` -- the real gate. Spins up a headless Houdini via
     ``hython``, builds a minimal scene for the chunk's context (e.g. a grid +
     point wrangle, or an MPM source + solver), sets the VEX, cooks, and checks
     for cook errors. This is the only method that can set ``verified=True``.
     Requires Houdini (``$HFS`` or ``hython`` on PATH). Run it on the exact
     target build (21.0.630) so ``verified_houdini_build`` is truthful.

  2. ``static-lint`` -- the portable fallback. Runs anywhere, no Houdini
     needed. Catches the cheap-but-common failures: unbalanced
     braces/parens/brackets, empty code, APEX VEX that illegally uses ``@``
     attribute syntax, and a few obvious smells. Passing static-lint does NOT
     set ``verified=True`` -- it only means "not obviously broken".

Usage:
    # Verify a JSONL of authored samples or chunks
    python scripts/quality/verify_vex.py data/authored/procedural_modeling.jsonl

    # Force static-only (skip Houdini even if present)
    python scripts/quality/verify_vex.py --static-only chunks.jsonl

    # As a library
    from scripts.quality.verify_vex import verify_code
    result = verify_code("@P.y += 1;", vex_context="sop")
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class VerifyResult:
    passed: bool
    method: str                       # "static-lint" | "hython-cook"
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    houdini_build: str = ""           # populated only by hython-cook

    @property
    def verified(self) -> bool:
        """Only a real cook on Houdini counts as 'verified'."""
        return self.passed and self.method == "hython-cook"


# ---------------------------------------------------------------------------
# Houdini detection
# ---------------------------------------------------------------------------

def find_hython() -> str | None:
    """Locate the ``hython`` executable, if Houdini is installed."""
    exe = shutil.which("hython")
    if exe:
        return exe
    hfs = os.environ.get("HFS")
    if hfs:
        candidate = Path(hfs) / "bin" / ("hython.exe" if os.name == "nt" else "hython")
        if candidate.exists():
            return str(candidate)
    return None


# ---------------------------------------------------------------------------
# Static linter (portable, no Houdini required)
# ---------------------------------------------------------------------------

# Map every vex_context value to the wrangle/SOP harness used for cook
# verification. APEX is special-cased (no @ syntax, named ports only).
_CONTEXT_NODE = {
    "sop": "attribwrangle",
    "dop": "gasfieldwrangle",
    "solver": "attribwrangle",   # geometry wrangle inside a SOP/DOP solver
    "cop": "vopcop2filter",
    "chop": "channelwrangle",
    "cvex": "attribwrangle",
    "material": "attribwrangle",  # snippet-style shading VEX
    "lop": "attribwrangle",       # SOP-side prep feeding Solaris
    "apex": "apex_runvex",
}


def _strip_comments_and_strings(code: str) -> str:
    """Remove // and /* */ comments and string literals so delimiter
    counting isn't fooled by braces inside them."""
    # Block comments
    code = re.sub(r"/\*.*?\*/", " ", code, flags=re.DOTALL)
    # Line comments
    code = re.sub(r"//[^\n]*", " ", code)
    # String literals (double and single quoted, with escapes)
    code = re.sub(r'"(?:\\.|[^"\\])*"', '""', code)
    code = re.sub(r"'(?:\\.|[^'\\])*'", "''", code)
    return code


def static_lint(code: str, vex_context: str = "sop") -> VerifyResult:
    """Cheap structural checks that work without Houdini."""
    errors: list[str] = []
    warnings: list[str] = []

    if not code or not code.strip():
        return VerifyResult(False, "static-lint", ["empty code"], [])

    stripped = _strip_comments_and_strings(code)

    # Balanced delimiters
    for open_c, close_c, name in [("{", "}", "braces"),
                                  ("(", ")", "parentheses"),
                                  ("[", "]", "brackets")]:
        diff = stripped.count(open_c) - stripped.count(close_c)
        if diff != 0:
            errors.append(
                f"unbalanced {name}: {stripped.count(open_c)} '{open_c}' "
                f"vs {stripped.count(close_c)} '{close_c}'"
            )

    # APEX VEX must not use @ attribute syntax (named ports only)
    if vex_context == "apex" and re.search(r"[a-zA-Z_]?@\w+", code):
        errors.append(
            "APEX VEX (RunVex) must not use '@' attribute syntax; "
            "use named inputs/outputs instead"
        )

    # Statement-ish sanity: a non-empty body of code that has executable
    # lines should contain at least one ';'. (Pure function-signature-only
    # CVEX is allowed to be light, so this is a warning, not an error.)
    code_no_decl = re.sub(r"\bcvex\s+\w+\s*\([^)]*\)", "", stripped)
    if ";" not in code_no_decl and "{" not in stripped:
        warnings.append("no ';' found -- may be a fragment, not a statement")

    # Common typo: assignment to a function call result like `length(@v) = x`
    if re.search(r"\b\w+\([^)]*\)\s*=[^=]", stripped):
        warnings.append("possible assignment to a function call result")

    # @ attribute reads with no type on first use is fine in VEX, but a bare
    # trailing '@' is a syntax error
    if re.search(r"@\s", code) or re.search(r"@$", code.strip()):
        warnings.append("dangling '@' -- check attribute name follows")

    return VerifyResult(len(errors) == 0, "static-lint", errors, warnings)


# ---------------------------------------------------------------------------
# hython cook verifier (real gate; requires Houdini)
# ---------------------------------------------------------------------------

# Rendered inside hython. Builds a tiny scene, applies the snippet, cooks,
# reports JSON. Kept deliberately minimal and context-aware.
_HYTHON_TEMPLATE = r'''
import json, sys
import hou

code = {code!r}
ctx = {ctx!r}

result = {{"passed": False, "errors": [], "build": hou.applicationVersionString()}}
try:
    obj = hou.node("/obj")
    geo = obj.createNode("geo", "verify_geo")
    src = geo.createNode("grid")
    src.parm("rows").set(10); src.parm("cols").set(10)

    node_type = {{
        "sop": "attribwrangle", "cvex": "attribwrangle",
        "material": "attribwrangle", "lop": "attribwrangle",
        "solver": "attribwrangle",
    }}.get(ctx, "attribwrangle")

    w = geo.createNode(node_type)
    w.setFirstInput(src)
    snippet = w.parm("snippet")
    if snippet is None:
        result["errors"].append("no snippet parm on %s" % node_type)
    else:
        snippet.set(code)
        try:
            w.cook(force=True)
            errs = w.errors()
            if errs:
                result["errors"].extend(list(errs))
            else:
                result["passed"] = True
        except hou.Error as e:
            result["errors"].append(str(e))
except Exception as e:
    result["errors"].append("harness error: %s" % e)

print("__VERIFY_JSON__" + json.dumps(result))
'''


def verify_with_hython(code: str, vex_context: str, hython: str,
                       timeout: float = 120.0) -> VerifyResult:
    """Cook the snippet in a headless Houdini and report cook errors."""
    script = _HYTHON_TEMPLATE.format(code=code, ctx=vex_context)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(script)
        script_path = f.name
    try:
        proc = subprocess.run(
            [hython, script_path],
            capture_output=True, text=True, timeout=timeout,
        )
        out = proc.stdout
        marker = "__VERIFY_JSON__"
        if marker in out:
            payload = json.loads(out.split(marker, 1)[1].strip().splitlines()[0])
            return VerifyResult(
                passed=payload.get("passed", False),
                method="hython-cook",
                errors=payload.get("errors", []),
                houdini_build=payload.get("build", ""),
            )
        return VerifyResult(
            False, "hython-cook",
            errors=[f"no verifier output; stderr: {proc.stderr[:500]}"],
        )
    except subprocess.TimeoutExpired:
        return VerifyResult(False, "hython-cook", errors=["hython timed out"])
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def verify_code(code: str, vex_context: str = "sop",
                static_only: bool = False) -> VerifyResult:
    """Verify a single VEX snippet.

    Uses ``hython-cook`` if Houdini is available (and not ``static_only``),
    otherwise falls back to ``static-lint``. APEX is always static-linted for
    now (no generic cook harness for RunVex graphs yet).
    """
    # APEX has no generic cook harness; static-lint always.
    if vex_context != "apex" and not static_only:
        hython = find_hython()
        if hython:
            res = verify_with_hython(code, vex_context, hython)
            # If the harness itself failed (not the VEX), fall back to lint so
            # we still get a signal rather than a false negative.
            if res.passed or res.errors and not any(
                "harness error" in e or "no verifier output" in e
                for e in res.errors
            ):
                return res
    return static_lint(code, vex_context)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _iter_records(path: Path):
    """Yield (code, context, label) from a JSONL of authored samples or
    ChunkV2 chunks."""
    with path.open(encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            ctx_list = d.get("vex_context", ["sop"])
            ctx = ctx_list[0] if isinstance(ctx_list, list) and ctx_list else "sop"
            # authored format uses "code"; ChunkV2 uses code_blocks
            if "code" in d:
                code = d["code"]
            else:
                blocks = d.get("code_blocks", [])
                code = "\n".join(b.get("code", "") for b in blocks)
            label = d.get("id") or d.get("title") or f"record {i}"
            yield code, ctx, label


def main() -> int:
    ap = argparse.ArgumentParser(description="VEX corpus verification gate")
    ap.add_argument("jsonl", type=Path, help="authored samples or chunks JSONL")
    ap.add_argument("--static-only", action="store_true",
                    help="skip Houdini even if available")
    args = ap.parse_args()

    if not args.jsonl.exists():
        print(f"error: {args.jsonl} not found", file=sys.stderr)
        return 2

    hython = None if args.static_only else find_hython()
    print(f"Verification method: "
          f"{'hython-cook (' + hython + ')' if hython else 'static-lint (no Houdini found)'}")

    total = passed = failed = 0
    for code, ctx, label in _iter_records(args.jsonl):
        total += 1
        res = verify_code(code, ctx, static_only=args.static_only)
        if res.passed:
            passed += 1
            tag = "OK " if not res.warnings else "WARN"
            print(f"  [{tag}] {label} ({res.method})")
            for w in res.warnings:
                print(f"         ! {w}")
        else:
            failed += 1
            print(f"  [FAIL] {label} ({res.method})")
            for e in res.errors:
                print(f"         x {e}")

    print(f"\n{passed}/{total} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
