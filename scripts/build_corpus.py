#!/usr/bin/env python3
"""Build the single canonical, ingestion-ready VEX corpus.

The corpus is produced from several sources that accumulate in separate files:

  - ``output/corpus/merged_corpus.jsonl``   legacy enriched chunks (llm_topic)
  - ``output/authored/authored_corpus.jsonl`` authored H21 samples (domain)
  - ``output/harvest/*.jsonl``               license-aware GitHub harvest

This script unifies them into ONE file -- ``output/corpus/vex_corpus.jsonl`` --
that downstream consumers (notably ``scripts/sync_to_synapse.py``) can ingest
without knowing where each chunk came from. It also:

  - **Normalizes every chunk** so it always carries a grouping key
    (``llm_topic``) and a ``domain``. This matters because the Synapse sync
    groups by ``llm_topic`` and *drops* chunks that lack it -- here we make sure
    nothing is silently lost.
  - **Backfills license/attribution** from ``config/sources.yaml`` and marks
    chunks from reference-only sources (e.g. SideFX docs) for redistribution
    review.
  - **Dedups** across all inputs by checksum.
  - Writes ``output/corpus/corpus_manifest.json`` -- the ingestion front door:
    counts by domain / context / source / license / verification.

Usage:
    python scripts/build_corpus.py
    python scripts/build_corpus.py --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.schema import PIPELINE_VERSION  # noqa: E402

SOURCES_YAML = PROJECT_ROOT / "config" / "sources.yaml"
CORPUS_DIR = PROJECT_ROOT / "output" / "corpus"
AUTHORED = PROJECT_ROOT / "output" / "authored" / "authored_corpus.jsonl"
BEST_PRACTICES = PROJECT_ROOT / "output" / "best_practices" / "best_practices_corpus.jsonl"
HARVEST_DIR = PROJECT_ROOT / "output" / "harvest"

LEGACY_CORPUS = CORPUS_DIR / "merged_corpus.jsonl"
CANONICAL = CORPUS_DIR / "vex_corpus.jsonl"
MANIFEST = CORPUS_DIR / "corpus_manifest.json"

# Legacy chunks (Joy of VEX / cgwiki / SideFX / blueprints) belong to the
# pre-H21 fundamentals body of work.
LEGACY_DOMAIN = "fundamentals"
FALLBACK_TOPIC = "uncategorized"


# ---------------------------------------------------------------------------
# Source license registry
# ---------------------------------------------------------------------------

def load_source_registry(path: Path = SOURCES_YAML) -> dict[str, dict]:
    """Map source_id -> {license, attribution, reference_only, authority}."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    registry = {}
    for s in data.get("sources", []):
        registry[s["id"]] = {
            "license": s.get("license", ""),
            "attribution": s.get("attribution", ""),
            "reference_only": bool(s.get("reference_only", False)),
            "authority": float(s.get("authority", 0.0)),
        }
    return registry


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _checksum(chunk: dict) -> str:
    if chunk.get("checksum"):
        return chunk["checksum"]
    parts = [chunk.get("content", "")]
    for b in chunk.get("code_blocks", []):
        parts.append(b.get("code", ""))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def normalize_chunk(chunk: dict, registry: dict[str, dict]) -> dict:
    """Ensure every chunk carries domain, llm_topic, license and review flags.

    Returns the same dict, mutated in place.
    """
    source_id = chunk.get("source_id", "")
    src = registry.get(source_id, {})

    # --- domain: authored/harvest chunks already set it; legacy -> fundamentals
    domain = chunk.get("domain") or ""
    if not domain:
        domain = LEGACY_DOMAIN
        chunk["domain"] = domain

    # --- grouping key for Synapse: keep llm_topic, else use domain, else fallback
    if not chunk.get("llm_topic"):
        # authored/harvest H21 chunks group under their domain; legacy
        # un-topiced chunks fall back so they are never dropped.
        chunk["llm_topic"] = domain if domain != LEGACY_DOMAIN else FALLBACK_TOPIC

    # --- license / attribution backfill from sources.yaml (don't overwrite
    #     a license a chunk already carries, e.g. authored MIT or harvested)
    if not chunk.get("license"):
        chunk["license"] = src.get("license", "")
    if not chunk.get("attribution"):
        chunk["attribution"] = src.get("attribution", "")

    # --- redistribution review: reference-only sources must not ship code
    has_code = any(b.get("code", "").strip() for b in chunk.get("code_blocks", []))
    chunk["redistribution_review"] = bool(src.get("reference_only") and has_code)

    # --- verification defaults (legacy chunks predate the gate)
    chunk.setdefault("verified", False)
    chunk.setdefault("verification_method", "")
    chunk.setdefault("verified_houdini_build", "")

    # --- ensure a checksum for dedup
    chunk["checksum"] = _checksum(chunk)
    return chunk


# ---------------------------------------------------------------------------
# Loading + merging
# ---------------------------------------------------------------------------

def _load_jsonl(path: Path) -> list[dict]:
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def collect_inputs() -> list[tuple[str, Path]]:
    """Ordered (label, path) inputs. Order = dedup precedence (first wins)."""
    inputs: list[tuple[str, Path]] = []
    if LEGACY_CORPUS.exists():
        inputs.append(("legacy", LEGACY_CORPUS))
    if AUTHORED.exists():
        inputs.append(("authored", AUTHORED))
    if BEST_PRACTICES.exists():
        inputs.append(("best_practices", BEST_PRACTICES))
    if HARVEST_DIR.exists():
        for p in sorted(HARVEST_DIR.glob("*.jsonl")):
            inputs.append((f"harvest:{p.stem}", p))
    return inputs


def build(registry: dict[str, dict]) -> tuple[list[dict], dict]:
    """Return (unified chunks, provenance counts)."""
    seen: set[str] = set()
    chunks: list[dict] = []
    provenance: Counter = Counter()

    for label, path in collect_inputs():
        for chunk in _load_jsonl(path):
            normalize_chunk(chunk, registry)
            cs = chunk["checksum"]
            if cs in seen:
                continue
            seen.add(cs)
            chunk["_origin"] = label
            chunks.append(chunk)
            provenance[label] += 1

    # Deterministic order: domain, source, id
    chunks.sort(key=lambda c: (c.get("domain", ""), c.get("source_id", ""),
                               c.get("id", "")))
    return chunks, dict(provenance)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def build_manifest(chunks: list[dict], provenance: dict) -> dict:
    by_domain: Counter = Counter()
    by_ctx: Counter = Counter()
    by_source: Counter = Counter()
    by_license: Counter = Counter()
    by_difficulty: Counter = Counter()
    verified = 0
    review = 0
    with_code = 0

    for c in chunks:
        by_domain[c.get("domain", "")] += 1
        for ctx in c.get("vex_context", []):
            by_ctx[ctx] += 1
        by_source[c.get("source_id", "")] += 1
        by_license[c.get("license", "") or "(none)"] += 1
        by_difficulty[c.get("difficulty", "")] += 1
        if c.get("verified"):
            verified += 1
        if c.get("redistribution_review"):
            review += 1
        if any(b.get("code", "").strip() for b in c.get("code_blocks", [])):
            with_code += 1

    return {
        "schema_version": PIPELINE_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "corpus_file": CANONICAL.name,
        "total_chunks": len(chunks),
        "with_code": with_code,
        "verified_chunks": verified,
        "redistribution_review_chunks": review,
        "provenance": provenance,
        "by_domain": dict(sorted(by_domain.items())),
        "by_vex_context": dict(sorted(by_ctx.items())),
        "by_source": dict(sorted(by_source.items())),
        "by_license": dict(sorted(by_license.items())),
        "by_difficulty": dict(sorted(by_difficulty.items())),
        "ingestion": {
            "grouping_key": "llm_topic (== domain for H21 content)",
            "command": "python scripts/sync_to_synapse.py --synapse /path/to/Synapse",
            "notes": (
                "Every chunk carries llm_topic and domain so none are dropped "
                "on sync. Chunks flagged redistribution_review come from "
                "reference-only sources and should not be redistributed "
                "verbatim -- resolve before publishing the synced output."
            ),
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Build canonical ingestion-ready corpus")
    ap.add_argument("--dry-run", action="store_true",
                    help="report only; do not write files")
    args = ap.parse_args()

    registry = load_source_registry()
    inputs = collect_inputs()
    if not inputs:
        print("No corpus inputs found.", file=sys.stderr)
        return 2

    print("Inputs:")
    for label, path in inputs:
        print(f"  {label:18s} {path.relative_to(PROJECT_ROOT)}")

    chunks, provenance = build(registry)
    manifest = build_manifest(chunks, provenance)

    print(f"\nUnified {manifest['total_chunks']} chunks "
          f"({manifest['with_code']} with code, "
          f"{manifest['verified_chunks']} verified, "
          f"{manifest['redistribution_review_chunks']} need redistribution review)")
    print("  by domain:  " + ", ".join(f"{k}={v}" for k, v in manifest["by_domain"].items()))
    print("  by license: " + ", ".join(f"{k}={v}" for k, v in manifest["by_license"].items()))

    if args.dry_run:
        print("\n[DRY RUN] No files written.")
        return 0

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    with CANONICAL.open("w", encoding="utf-8") as f:
        for c in chunks:
            c.pop("_origin", None)  # internal only
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")

    print(f"\nWrote {CANONICAL.relative_to(PROJECT_ROOT)}")
    print(f"Wrote {MANIFEST.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
