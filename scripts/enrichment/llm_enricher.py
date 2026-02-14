"""Batch LLM enrichment for VEX corpus chunks.

Bridges the ChunkV2 corpus with the Ollama pipeline to fill LLM-generated
fields: prompt, explanation, alternative_prompts, and quality metadata.

Processing tiers:
  Tier 1 (nemotron-mini, ~1s/task): classify_topic, detect_bugs, estimate_complexity
  Tier 2 (nemotron-3-nano, ~15s/task): generate_prompt, generate_explanation

Only processes chunks that have code and are missing the target fields.
Supports incremental runs -- already-enriched chunks are skipped.

Usage:
    python scripts/enrichment/llm_enricher.py                     # Full run
    python scripts/enrichment/llm_enricher.py --tier 1            # Tier 1 only
    python scripts/enrichment/llm_enricher.py --tier 2            # Tier 2 only
    python scripts/enrichment/llm_enricher.py --limit 10          # Process 10 chunks
    python scripts/enrichment/llm_enricher.py --dry-run           # Preview what would run
    python scripts/enrichment/llm_enricher.py --concurrency 3     # Parallel requests
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ollama.src.client import OllamaClient
from ollama.src.dispatcher import TaskDispatcher


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Tier 1 tasks to run on every chunk with code
TIER1_TASKS = ["classify_topic", "detect_bugs", "estimate_complexity"]

# Tier 2 tasks to run on chunks missing prompt/explanation
TIER2_TASKS = ["generate_prompt", "generate_explanation"]

# Fields that indicate a chunk has already been LLM-processed
TIER1_DONE_FIELDS = ["llm_topic", "llm_bugs", "llm_complexity"]
TIER2_DONE_FIELDS = ["prompt", "explanation"]

# Default concurrency per tier
DEFAULT_CONCURRENCY = {1: 5, 2: 2}


# ---------------------------------------------------------------------------
# Chunk selection
# ---------------------------------------------------------------------------

def _get_code(chunk: dict) -> str:
    """Extract all code text from a chunk."""
    parts = []
    for cb in chunk.get("code_blocks", []):
        if isinstance(cb, dict):
            parts.append(cb.get("code", ""))
        else:
            parts.append(str(cb))
    if chunk.get("code"):
        parts.append(chunk["code"])
    return "\n\n".join(parts).strip()


def needs_tier1(chunk: dict) -> bool:
    """Check if chunk needs Tier 1 processing."""
    code = _get_code(chunk)
    if not code or len(code) < 10:
        return False
    # Skip if already has LLM classification
    return not chunk.get("llm_topic")


def needs_tier2(chunk: dict) -> bool:
    """Check if chunk needs Tier 2 processing."""
    code = _get_code(chunk)
    if not code or len(code) < 10:
        return False
    # Skip if already has prompt and explanation
    return not chunk.get("prompt") or not chunk.get("explanation")


# ---------------------------------------------------------------------------
# Task processing
# ---------------------------------------------------------------------------

async def process_tier1_chunk(
    dispatcher: TaskDispatcher,
    chunk: dict,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Run all Tier 1 tasks on a single chunk."""
    async with semaphore:
        cid = chunk.get("id", "?")
        code = _get_code(chunk)
        input_data = {"code": code}
        results = {}

        for task_type in TIER1_TASKS:
            try:
                # Use process_task directly (bypasses consensus for speed)
                result = await dispatcher.client.process_task(
                    task_type, input_data, task_id=cid,
                )
                if result.status.value == "success":
                    results[task_type] = result.result
                    results[f"{task_type}_confidence"] = result.confidence
            except Exception as e:
                results[f"{task_type}_error"] = str(e)

        return results


async def process_tier2_chunk(
    dispatcher: TaskDispatcher,
    chunk: dict,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Run Tier 2 tasks on a single chunk."""
    async with semaphore:
        cid = chunk.get("id", "?")
        code = _get_code(chunk)
        input_data = {"code": code}
        results = {}

        for task_type in TIER2_TASKS:
            # Skip if chunk already has this field
            if task_type == "generate_prompt" and chunk.get("prompt"):
                continue
            if task_type == "generate_explanation" and chunk.get("explanation"):
                continue

            try:
                result = await dispatcher.client.process_task(
                    task_type, input_data, task_id=cid,
                )
                if result.status.value == "success":
                    results[task_type] = result.result
                    results[f"{task_type}_confidence"] = result.confidence
                    # Flag low confidence
                    if dispatcher.should_flag_for_review(result):
                        results[f"{task_type}_flagged"] = True
            except Exception as e:
                results[f"{task_type}_error"] = str(e)

        return results


def apply_tier1_results(chunk: dict, results: dict) -> list[str]:
    """Apply Tier 1 LLM results to a chunk. Returns list of changes."""
    changes = []

    # classify_topic
    topic_result = results.get("classify_topic", {})
    if topic_result:
        chunk["llm_topic"] = topic_result.get("primary_topic", "")
        chunk["llm_secondary_topics"] = topic_result.get("secondary_topics", [])
        changes.append(f"topic: {chunk['llm_topic']}")

    # detect_bugs
    bugs_result = results.get("detect_bugs", {})
    if bugs_result:
        bugs = bugs_result.get("bugs", [])
        chunk["llm_bugs"] = bugs
        chunk["llm_code_quality"] = bugs_result.get("overall_quality", "unknown")
        if bugs:
            changes.append(f"bugs: {len(bugs)} found")
        else:
            changes.append("bugs: clean")

    # estimate_complexity
    complexity_result = results.get("estimate_complexity", {})
    if complexity_result:
        chunk["llm_complexity"] = complexity_result.get("complexity", "unknown")
        chunk["llm_bottlenecks"] = complexity_result.get("bottlenecks", [])
        changes.append(f"complexity: {chunk['llm_complexity']}")

    return changes


def apply_tier2_results(chunk: dict, results: dict) -> list[str]:
    """Apply Tier 2 LLM results to a chunk. Returns list of changes."""
    changes = []

    # generate_prompt
    prompt_result = results.get("generate_prompt", {})
    if prompt_result:
        prompt = prompt_result.get("prompt", "")
        if prompt and not chunk.get("prompt"):
            chunk["prompt"] = prompt
            changes.append(f"prompt: {prompt[:60]}...")
        alt = prompt_result.get("alternative_phrasings", [])
        if alt and not chunk.get("alternative_prompts"):
            chunk["alternative_prompts"] = alt
            changes.append(f"alt_prompts: {len(alt)}")

    # generate_explanation
    explanation_result = results.get("generate_explanation", {})
    if explanation_result:
        explanation = explanation_result.get("explanation", "")
        if explanation and not chunk.get("explanation"):
            chunk["explanation"] = explanation
            changes.append(f"explanation: {len(explanation)} chars")
        concepts = explanation_result.get("key_concepts", [])
        if concepts:
            chunk["llm_key_concepts"] = concepts

    # Flag for review if any task was flagged
    for key in results:
        if key.endswith("_flagged") and results[key]:
            chunk["flagged_for_review"] = True
            chunk["review_reason"] = chunk.get("review_reason", "") + " Low LLM confidence."
            changes.append("FLAGGED for review")
            break

    return changes


# ---------------------------------------------------------------------------
# Main enrichment loop
# ---------------------------------------------------------------------------

async def enrich_with_llm(
    input_path: Path | None = None,
    output_path: Path | None = None,
    tier: int | None = None,
    limit: int | None = None,
    concurrency: int | None = None,
    dry_run: bool = False,
) -> dict:
    """Run LLM enrichment on the corpus."""
    input_path = input_path or (PROJECT_ROOT / "output" / "corpus" / "merged_corpus.jsonl")
    output_path = output_path or input_path

    if not input_path.exists():
        print(f"Error: {input_path} not found.")
        return {}

    # Load corpus
    chunks = []
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))

    print(f"Loaded {len(chunks)} chunks from {input_path}")

    # Filter chunks needing processing
    run_tier1 = tier is None or tier == 1
    run_tier2 = tier is None or tier == 2

    tier1_chunks = [c for c in chunks if needs_tier1(c)] if run_tier1 else []
    tier2_chunks = [c for c in chunks if needs_tier2(c)] if run_tier2 else []

    if limit:
        tier1_chunks = tier1_chunks[:limit]
        tier2_chunks = tier2_chunks[:limit]

    print(f"\nChunks to process:")
    if run_tier1:
        print(f"  Tier 1 (classify/bugs/complexity): {len(tier1_chunks)}")
    if run_tier2:
        print(f"  Tier 2 (prompt/explanation): {len(tier2_chunks)}")

    if dry_run:
        print("\n[dry-run] Would process above chunks. Exiting.")
        if tier1_chunks:
            print(f"\nSample Tier 1 candidates:")
            for c in tier1_chunks[:5]:
                code = _get_code(c)
                print(f"  {c.get('id','?')}: {len(code)} chars of code")
        if tier2_chunks:
            print(f"\nSample Tier 2 candidates:")
            for c in tier2_chunks[:5]:
                print(f"  {c.get('id','?')}: prompt={'yes' if c.get('prompt') else 'no'}, "
                      f"explanation={'yes' if c.get('explanation') else 'no'}")
        return {"tier1_pending": len(tier1_chunks), "tier2_pending": len(tier2_chunks)}

    # Initialize Ollama client
    client = OllamaClient()
    health = await client.health_check()
    print(f"\nOllama status: {health['status']}")
    if health["status"] == "offline":
        print("Error: Ollama is not running. Start it with `ollama serve`.")
        return {}
    print(f"  Available models: {', '.join(health['available_models'])}")

    dispatcher = TaskDispatcher(client=client)
    stats = {"tier1_processed": 0, "tier2_processed": 0, "errors": 0, "flagged": 0}

    # Index chunks by ID for update
    chunk_index = {c.get("id", ""): c for c in chunks}

    # --- Tier 1 ---
    if run_tier1 and tier1_chunks:
        t1_concurrency = concurrency or DEFAULT_CONCURRENCY[1]
        semaphore = asyncio.Semaphore(t1_concurrency)
        print(f"\n{'='*50}")
        print(f"TIER 1: Processing {len(tier1_chunks)} chunks (concurrency={t1_concurrency})")
        print(f"{'='*50}")

        t0 = time.time()
        batch_size = 20
        for batch_start in range(0, len(tier1_chunks), batch_size):
            batch = tier1_chunks[batch_start:batch_start + batch_size]
            tasks = [
                process_tier1_chunk(dispatcher, c, semaphore)
                for c in batch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for chunk, result in zip(batch, results):
                cid = chunk.get("id", "?")
                if isinstance(result, Exception):
                    print(f"  ERROR {cid}: {result}")
                    stats["errors"] += 1
                    continue

                changes = apply_tier1_results(chunk, result)
                if changes:
                    stats["tier1_processed"] += 1
                    print(f"  {cid}: {', '.join(changes)}")

            elapsed = time.time() - t0
            processed = min(batch_start + batch_size, len(tier1_chunks))
            rate = processed / elapsed if elapsed > 0 else 0
            print(f"  [{processed}/{len(tier1_chunks)}] {rate:.1f} chunks/s")

    # --- Tier 2 ---
    if run_tier2 and tier2_chunks:
        t2_concurrency = concurrency or DEFAULT_CONCURRENCY[2]
        semaphore = asyncio.Semaphore(t2_concurrency)
        print(f"\n{'='*50}")
        print(f"TIER 2: Processing {len(tier2_chunks)} chunks (concurrency={t2_concurrency})")
        print(f"{'='*50}")

        t0 = time.time()
        batch_size = 5
        for batch_start in range(0, len(tier2_chunks), batch_size):
            batch = tier2_chunks[batch_start:batch_start + batch_size]
            tasks = [
                process_tier2_chunk(dispatcher, c, semaphore)
                for c in batch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for chunk, result in zip(batch, results):
                cid = chunk.get("id", "?")
                if isinstance(result, Exception):
                    print(f"  ERROR {cid}: {result}")
                    stats["errors"] += 1
                    continue

                changes = apply_tier2_results(chunk, result)
                if changes:
                    stats["tier2_processed"] += 1
                    if chunk.get("flagged_for_review"):
                        stats["flagged"] += 1
                    print(f"  {cid}: {', '.join(changes)}")

            elapsed = time.time() - t0
            processed = min(batch_start + batch_size, len(tier2_chunks))
            rate = processed / elapsed if elapsed > 0 else 0
            remaining = (len(tier2_chunks) - processed) / rate if rate > 0 else 0
            print(f"  [{processed}/{len(tier2_chunks)}] {rate:.1f} chunks/s, ~{remaining:.0f}s remaining")

    # Write results
    print(f"\nWriting enriched corpus...")
    with open(output_path, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"  Written to: {output_path}")

    print(f"\nLLM Enrichment Summary:")
    print(f"  Tier 1 processed: {stats['tier1_processed']}")
    print(f"  Tier 2 processed: {stats['tier2_processed']}")
    print(f"  Errors: {stats['errors']}")
    print(f"  Flagged for review: {stats['flagged']}")

    return stats


def main():
    parser = argparse.ArgumentParser(description="LLM enrichment for VEX corpus")
    parser.add_argument("--input", type=Path, help="Input JSONL corpus")
    parser.add_argument("--output", type=Path, help="Output JSONL (default: overwrite input)")
    parser.add_argument("--tier", type=int, choices=[1, 2], help="Run only this tier")
    parser.add_argument("--limit", type=int, help="Max chunks to process")
    parser.add_argument("--concurrency", type=int, help="Parallel Ollama requests")
    parser.add_argument("--dry-run", action="store_true", help="Preview without processing")
    args = parser.parse_args()

    asyncio.run(enrich_with_llm(
        input_path=args.input,
        output_path=args.output,
        tier=args.tier,
        limit=args.limit,
        concurrency=args.concurrency,
        dry_run=args.dry_run,
    ))


if __name__ == "__main__":
    main()
