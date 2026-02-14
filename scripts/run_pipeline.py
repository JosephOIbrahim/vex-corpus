"""Pipeline orchestrator for VEX corpus.

Runs all pipeline stages in order:
  1. Scrape sources (cgwiki, sidefx, local)
  2. Merge into unified corpus
  3. Enrich (VEX extraction, auto-tagging, prerequisites)
  4. Quality checks (dedup report)
  5. LLM enrichment (Ollama Tier 1 + Tier 2)
  6. Index (BM25 + semantic search index)
  7. Evaluate (retrieval quality metrics)

Usage:
    python scripts/run_pipeline.py                    # Run stages 1-4 (no Ollama)
    python scripts/run_pipeline.py --stage merge      # Run from merge onward
    python scripts/run_pipeline.py --stage enrich     # Run enrichment only
    python scripts/run_pipeline.py --stage llm        # Run LLM enrichment
    python scripts/run_pipeline.py --stage index      # Build search index
    python scripts/run_pipeline.py --stage eval       # Run evaluation
    python scripts/run_pipeline.py --all              # Run everything (incl. LLM + index)
    python scripts/run_pipeline.py --dry-run          # Preview all stages
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Pipeline stage order
STAGES = ["scrape", "merge", "enrich", "quality", "llm", "index", "eval"]

# Default stages (without Ollama/embedding which are slow)
DEFAULT_STAGES = ["scrape", "merge", "enrich", "quality"]


def _log(msg: str):
    print(f"[pipeline] {msg}")


def _run_timed(label: str, func, **kwargs) -> tuple:
    """Run a function with timing. Returns (result, elapsed_seconds)."""
    _log(f"Starting: {label}")
    t0 = time.time()
    try:
        result = func(**kwargs)
        elapsed = time.time() - t0
        _log(f"Completed: {label} ({elapsed:.1f}s)")
        return result, elapsed
    except Exception as e:
        elapsed = time.time() - t0
        _log(f"FAILED: {label} ({elapsed:.1f}s) - {e}")
        raise


async def _run_timed_async(label: str, func, **kwargs) -> tuple:
    """Run an async function with timing."""
    _log(f"Starting: {label}")
    t0 = time.time()
    try:
        result = await func(**kwargs)
        elapsed = time.time() - t0
        _log(f"Completed: {label} ({elapsed:.1f}s)")
        return result, elapsed
    except Exception as e:
        elapsed = time.time() - t0
        _log(f"FAILED: {label} ({elapsed:.1f}s) - {e}")
        raise


# ---------------------------------------------------------------------------
# Stage: Scrape
# ---------------------------------------------------------------------------

def stage_scrape(dry_run: bool = False, sources: list[str] | None = None):
    """Run all scrapers."""
    timings = {}

    if not sources or "cgwiki" in sources:
        from scripts.scrapers.scrape_cgwiki import scrape_all
        if dry_run:
            _log("[dry-run] Would scrape cgwiki VEX pages")
        else:
            _, elapsed = _run_timed("cgwiki scraper", scrape_all)
            timings["cgwiki"] = elapsed

    if not sources or "sidefx" in sources:
        from scripts.scrapers.scrape_sidefx_vex import scrape_all as scrape_sidefx
        if dry_run:
            _log("[dry-run] Would scrape SideFX VEX reference")
        else:
            _, elapsed = _run_timed("sidefx scraper", scrape_sidefx)
            timings["sidefx"] = elapsed

    if not sources or "local" in sources:
        from scripts.ingest_local import ingest_directory
        if dry_run:
            _log("[dry-run] Would ingest local markdown files")
        else:
            local_dir = PROJECT_ROOT / "data" / "blueprints"
            if local_dir.exists():
                _, elapsed = _run_timed("local ingester", ingest_directory, input_dir=local_dir)
                timings["local"] = elapsed
            else:
                _log(f"Skipping local ingest: {local_dir} not found")

    return timings


# ---------------------------------------------------------------------------
# Stage: Merge
# ---------------------------------------------------------------------------

def stage_merge(dry_run: bool = False):
    """Merge all sources into unified corpus."""
    from scripts.merge_sources import merge_all

    if dry_run:
        _log("[dry-run] Would merge all sources into output/corpus/merged_corpus.jsonl")
        return None, 0

    return _run_timed("merge sources", merge_all)


# ---------------------------------------------------------------------------
# Stage: Enrich
# ---------------------------------------------------------------------------

def stage_enrich(dry_run: bool = False):
    """Run all enrichment scripts."""
    timings = {}

    from scripts.enrichment.vex_extractor import enrich_corpus
    if dry_run:
        _log("[dry-run] Would run VEX function extraction and validation")
    else:
        _, elapsed = _run_timed("VEX extractor", enrich_corpus)
        timings["vex_extractor"] = elapsed

    from scripts.enrichment.auto_tagger import tag_corpus
    if dry_run:
        _log("[dry-run] Would run auto-tagger")
    else:
        _, elapsed = _run_timed("auto-tagger", tag_corpus)
        timings["auto_tagger"] = elapsed

    from scripts.enrichment.prereq_linker import enrich_with_prereqs
    if dry_run:
        _log("[dry-run] Would build prerequisite graph")
    else:
        _, elapsed = _run_timed("prereq linker", enrich_with_prereqs)
        timings["prereq_linker"] = elapsed

    return timings


# ---------------------------------------------------------------------------
# Stage: Quality
# ---------------------------------------------------------------------------

def stage_quality(dry_run: bool = False):
    """Run quality checks."""
    from scripts.quality.dedup_report import generate_report

    if dry_run:
        _log("[dry-run] Would generate dedup report")
        return None, 0

    return _run_timed("dedup report", generate_report)


# ---------------------------------------------------------------------------
# Stage: LLM (Ollama)
# ---------------------------------------------------------------------------

async def stage_llm(dry_run: bool = False, limit: int | None = None):
    """Run LLM enrichment via Ollama."""
    from scripts.enrichment.llm_enricher import enrich_with_llm

    if dry_run:
        _log("[dry-run] Would run Ollama LLM enrichment (Tier 1 + Tier 2)")
        return await enrich_with_llm(dry_run=True)

    return await _run_timed_async("LLM enrichment", enrich_with_llm, limit=limit)


# ---------------------------------------------------------------------------
# Stage: Index
# ---------------------------------------------------------------------------

async def stage_index(dry_run: bool = False, bm25_only: bool = False):
    """Build search index."""
    from scripts.search.build_index import build_index

    if dry_run:
        _log("[dry-run] Would build search index (BM25 + semantic)")
        return await build_index(dry_run=True)

    return await _run_timed_async("search index", build_index, bm25_only=bm25_only)


# ---------------------------------------------------------------------------
# Stage: Evaluate
# ---------------------------------------------------------------------------

async def stage_eval(dry_run: bool = False, bm25_only: bool = False):
    """Run evaluation harness."""
    from scripts.evaluation.eval_harness import run_evaluation

    if dry_run:
        _log("[dry-run] Would run evaluation harness")
        return {}

    return await _run_timed_async("evaluation", run_evaluation, bm25_only=bm25_only)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def run_pipeline(
    stage: str | None = None,
    sources: list[str] | None = None,
    dry_run: bool = False,
    run_all: bool = False,
    llm_limit: int | None = None,
    bm25_only: bool = False,
):
    """Run the full pipeline or from a specific stage."""
    start_time = time.time()

    if run_all:
        stages_to_run = STAGES
    elif stage:
        stage_idx = STAGES.index(stage)
        stages_to_run = STAGES[stage_idx:]
    else:
        stages_to_run = DEFAULT_STAGES

    _log(f"Pipeline starting{' (dry run)' if dry_run else ''}")
    _log(f"Stages: {' -> '.join(stages_to_run)}")
    print()

    all_timings = {}

    try:
        if "scrape" in stages_to_run:
            _log("=" * 50)
            _log("STAGE 1: SCRAPE")
            _log("=" * 50)
            timings = stage_scrape(dry_run=dry_run, sources=sources)
            all_timings["scrape"] = timings
            print()

        if "merge" in stages_to_run:
            _log("=" * 50)
            _log("STAGE 2: MERGE")
            _log("=" * 50)
            stage_merge(dry_run=dry_run)
            print()

        if "enrich" in stages_to_run:
            _log("=" * 50)
            _log("STAGE 3: ENRICH")
            _log("=" * 50)
            timings = stage_enrich(dry_run=dry_run)
            all_timings["enrich"] = timings
            print()

        if "quality" in stages_to_run:
            _log("=" * 50)
            _log("STAGE 4: QUALITY")
            _log("=" * 50)
            stage_quality(dry_run=dry_run)
            print()

        if "llm" in stages_to_run:
            _log("=" * 50)
            _log("STAGE 5: LLM ENRICHMENT (Ollama)")
            _log("=" * 50)
            await stage_llm(dry_run=dry_run, limit=llm_limit)
            print()

        if "index" in stages_to_run:
            _log("=" * 50)
            _log("STAGE 6: SEARCH INDEX")
            _log("=" * 50)
            await stage_index(dry_run=dry_run, bm25_only=bm25_only)
            print()

        if "eval" in stages_to_run:
            _log("=" * 50)
            _log("STAGE 7: EVALUATION")
            _log("=" * 50)
            await stage_eval(dry_run=dry_run, bm25_only=bm25_only)
            print()

    except Exception as e:
        _log(f"Pipeline failed: {e}")
        raise

    total_time = time.time() - start_time
    _log("=" * 50)
    _log(f"Pipeline complete! Total time: {total_time:.1f}s")
    _log("=" * 50)

    return all_timings


def main():
    parser = argparse.ArgumentParser(description="Run VEX corpus pipeline")
    parser.add_argument("--stage", choices=STAGES,
                        help="Start from this stage (default: run stages 1-4)")
    parser.add_argument("--all", action="store_true",
                        help="Run all stages including LLM, index, and eval")
    parser.add_argument("--source", action="append", dest="sources",
                        help="Specific source to scrape (can repeat)")
    parser.add_argument("--llm-limit", type=int,
                        help="Max chunks for LLM enrichment")
    parser.add_argument("--bm25-only", action="store_true",
                        help="Skip semantic embeddings in index/eval")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview pipeline without executing")
    args = parser.parse_args()

    asyncio.run(run_pipeline(
        stage=args.stage,
        sources=args.sources,
        dry_run=args.dry_run,
        run_all=args.all,
        llm_limit=args.llm_limit,
        bm25_only=args.bm25_only,
    ))


if __name__ == "__main__":
    main()
