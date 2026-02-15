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

import httpx

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
DEFAULT_CONCURRENCY = {1: 3, 2: 2}

# Combined Tier 1 prompt -- single call does all 3 tasks
COMBINED_TIER1_SYSTEM = """You are a VEX (Houdini's Vector Expression Language) code analyzer.
Analyze the VEX code and provide ALL THREE analyses in a single JSON response.

Topics: point_cloud_ops, optimization_patterns, field_analysis, flow_visualization,
edge_topology, subdivision_surfaces, attribute_operations, debugging_patterns,
math_operations, color_operations, string_operations, geometry_creation,
noise_patterns, channel_references, matrix_transforms, quaternion_operations,
loop_patterns, conditional_logic, simulation_setup, rendering_setup

Common VEX bugs:
- resource_leak: pcopen() without pcclose()
- context_confusion: @ptnum in prim context
- type_mismatch: float to vector
- integer_division: 1/2 = 0
- uninitialized_attr: reading missing @attr

Complexity: O(1) no loops | O(n) single pass | O(n log n) KD-tree | O(n^2) nested loops

Respond with ONLY valid JSON:
{
  "topic": {"primary": "...", "secondary": [], "confidence": 0.9},
  "bugs": {"issues": [{"type": "...", "severity": "error|warning|info", "description": "..."}], "quality": "clean|minor_issues|major_issues"},
  "complexity": {"class": "O(1)|O(n)|O(n log n)|O(n^2)", "bottlenecks": [], "confidence": 0.9}
}"""

# Combined Tier 2 prompt -- single call for prompt + explanation
COMBINED_TIER2_SYSTEM = """You are a VEX training data generator for Houdini.
Given VEX code, provide BOTH a natural language prompt AND an explanation.

The prompt should be what a student would ask to produce this code.
The explanation should teach what the code does and why.

Respond with ONLY valid JSON:
{
  "prompt": "A natural instruction that would lead to writing this code",
  "alternative_prompts": ["2-3 alternative phrasings"],
  "explanation": "Clear explanation of what this code does and why each part matters",
  "key_concepts": ["list of VEX concepts used"]
}"""


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

async def process_tier1_combined(
    http_client: httpx.AsyncClient,
    base_url: str,
    model: str,
    chunk: dict,
    semaphore: asyncio.Semaphore,
    config: dict,
) -> dict:
    """Run all Tier 1 tasks in a SINGLE Ollama call."""
    async with semaphore:
        code = _get_code(chunk)
        prompt = f"VEX Code:\n```vex\n{code}\n```\n\nAnalyze this VEX code."

        payload = {
            "model": model,
            "prompt": prompt,
            "system": COMBINED_TIER1_SYSTEM,
            "stream": False,
            "options": {
                "temperature": config.get("temperature", 0.1),
                "top_p": config.get("top_p", 0.9),
                "num_predict": config.get("num_predict", 1024),
            },
            "format": "json",
        }

        try:
            response = await http_client.post(
                f"{base_url}/api/generate",
                json=payload,
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()
            content = data.get("response", "").strip()

            # Parse JSON from response
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            content = content.strip()

            parsed = json.loads(content)
            return {"combined": parsed}
        except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
            return {"error": f"HTTP: {e}"}
        except json.JSONDecodeError as e:
            return {"error": f"JSON parse: {e}", "raw": content[:200] if content else ""}
        except Exception as e:
            return {"error": str(e)}


async def process_tier2_combined(
    http_client: httpx.AsyncClient,
    base_url: str,
    model: str,
    chunk: dict,
    semaphore: asyncio.Semaphore,
    config: dict,
) -> dict:
    """Run Tier 2 tasks (prompt + explanation) in a SINGLE Ollama call."""
    async with semaphore:
        code = _get_code(chunk)
        prompt = f"VEX Code:\n```vex\n{code}\n```\n\nGenerate a training prompt and explanation."

        payload = {
            "model": model,
            "prompt": prompt,
            "system": COMBINED_TIER2_SYSTEM,
            "stream": False,
            "options": {
                "temperature": config.get("temperature", 0.3),
                "top_p": config.get("top_p", 0.95),
                "num_predict": config.get("num_predict", 2048),
            },
            "format": "json",
        }

        try:
            response = await http_client.post(
                f"{base_url}/api/generate",
                json=payload,
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()
            content = data.get("response", "").strip()

            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            content = content.strip()

            parsed = json.loads(content)
            return {"combined": parsed}
        except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
            return {"error": f"HTTP: {e}"}
        except json.JSONDecodeError as e:
            return {"error": f"JSON parse: {e}", "raw": content[:200] if content else ""}
        except Exception as e:
            return {"error": str(e)}


def apply_tier1_results(chunk: dict, results: dict) -> list[str]:
    """Apply Tier 1 LLM results to a chunk. Returns list of changes."""
    changes = []

    if "error" in results:
        changes.append(f"ERROR: {results['error'][:80]}")
        return changes

    combined = results.get("combined", {})

    # Topic classification — handle varying response shapes
    topic = combined.get("topic", {})
    if isinstance(topic, str):
        chunk["llm_topic"] = topic
        chunk["llm_secondary_topics"] = []
    elif isinstance(topic, dict):
        chunk["llm_topic"] = topic.get("primary", topic.get("primary_topic", ""))
        chunk["llm_secondary_topics"] = topic.get("secondary", topic.get("secondary_topics", []))
    if chunk.get("llm_topic"):
        changes.append(f"topic: {chunk['llm_topic']}")

    # Bug detection — model may return list or dict
    bugs = combined.get("bugs", {})
    if isinstance(bugs, list):
        chunk["llm_bugs"] = bugs
        chunk["llm_code_quality"] = "minor_issues" if bugs else "clean"
    elif isinstance(bugs, dict):
        issues = bugs.get("issues", bugs.get("bugs", []))
        if isinstance(issues, list):
            chunk["llm_bugs"] = issues
        else:
            chunk["llm_bugs"] = []
        chunk["llm_code_quality"] = bugs.get("quality", bugs.get("overall_quality", "unknown"))
    else:
        chunk["llm_bugs"] = []
        chunk["llm_code_quality"] = "unknown"
    n_bugs = len(chunk.get("llm_bugs", []))
    changes.append(f"bugs: {n_bugs}" if n_bugs else "clean")

    # Complexity estimation — handle varying shapes
    complexity = combined.get("complexity", {})
    if isinstance(complexity, str):
        chunk["llm_complexity"] = complexity
        chunk["llm_bottlenecks"] = []
    elif isinstance(complexity, dict):
        chunk["llm_complexity"] = complexity.get("class", complexity.get("complexity", "unknown"))
        chunk["llm_bottlenecks"] = complexity.get("bottlenecks", [])
    if chunk.get("llm_complexity"):
        changes.append(chunk["llm_complexity"])

    return changes


def apply_tier2_results(chunk: dict, results: dict) -> list[str]:
    """Apply Tier 2 LLM results to a chunk. Returns list of changes."""
    changes = []

    if "error" in results:
        changes.append(f"ERROR: {results['error'][:80]}")
        return changes

    combined = results.get("combined", {})

    # Prompt
    prompt = combined.get("prompt", "")
    if prompt and not chunk.get("prompt"):
        chunk["prompt"] = prompt
        changes.append(f"prompt: {prompt[:50]}...")

    alt = combined.get("alternative_prompts", [])
    if alt and not chunk.get("alternative_prompts"):
        chunk["alternative_prompts"] = alt
        changes.append(f"alt: {len(alt)}")

    # Explanation
    explanation = combined.get("explanation", "")
    if explanation and not chunk.get("explanation"):
        chunk["explanation"] = explanation
        changes.append(f"expl: {len(explanation)}ch")

    concepts = combined.get("key_concepts", [])
    if concepts:
        chunk["llm_key_concepts"] = concepts

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

    # Initialize Ollama client (for health check only)
    client = OllamaClient()
    health = await client.health_check()
    print(f"\nOllama status: {health['status']}")
    if health["status"] == "offline":
        print("Error: Ollama is not running. Start it with `ollama serve`.")
        return {}
    print(f"  Available models: {', '.join(health['available_models'])}")

    # Load model config
    base_url = client.base_url
    t1_model_config = client.get_model_for_tier(1)
    t2_model_config = client.get_model_for_tier(2)

    stats = {"tier1_processed": 0, "tier2_processed": 0, "errors": 0, "flagged": 0}

    # Use a SHARED httpx client for connection pooling
    async with httpx.AsyncClient() as http_client:

        # --- Tier 1 ---
        if run_tier1 and tier1_chunks:
            t1_concurrency = concurrency or DEFAULT_CONCURRENCY[1]
            semaphore = asyncio.Semaphore(t1_concurrency)
            model = t1_model_config.primary
            config = dict(t1_model_config.config)
            print(f"\n{'='*50}")
            print(f"TIER 1: Processing {len(tier1_chunks)} chunks")
            print(f"  Model: {model}, Concurrency: {t1_concurrency}")
            print(f"{'='*50}")

            t0 = time.time()
            completed = 0
            total = len(tier1_chunks)

            async def _process_t1(chunk):
                nonlocal completed
                cid = chunk.get("id", "?")
                try:
                    result = await process_tier1_combined(
                        http_client, base_url, model, chunk, semaphore, config,
                    )
                    changes = apply_tier1_results(chunk, result)
                    if changes and "ERROR" not in changes[0]:
                        stats["tier1_processed"] += 1
                    elif "ERROR" in (changes[0] if changes else ""):
                        stats["errors"] += 1
                    completed += 1
                    elapsed = time.time() - t0
                    rate = completed / elapsed if elapsed > 0 else 0
                    remaining = (total - completed) / rate if rate > 0 else 0
                    print(f"  [{completed}/{total}] {cid}: {', '.join(changes)} ({rate:.2f}/s, ~{remaining/60:.0f}m)", flush=True)
                except Exception as e:
                    print(f"  ERROR {cid}: {e}", flush=True)
                    stats["errors"] += 1

            await asyncio.gather(*[_process_t1(c) for c in tier1_chunks])

        # --- Tier 2 ---
        if run_tier2 and tier2_chunks:
            t2_concurrency = concurrency or DEFAULT_CONCURRENCY[2]
            semaphore = asyncio.Semaphore(t2_concurrency)
            model = t2_model_config.primary
            config = dict(t2_model_config.config)
            print(f"\n{'='*50}")
            print(f"TIER 2: Processing {len(tier2_chunks)} chunks")
            print(f"  Model: {model}, Concurrency: {t2_concurrency}")
            print(f"{'='*50}")

            t0 = time.time()
            completed = 0
            total = len(tier2_chunks)

            async def _process_t2(chunk):
                nonlocal completed
                cid = chunk.get("id", "?")
                try:
                    result = await process_tier2_combined(
                        http_client, base_url, model, chunk, semaphore, config,
                    )
                    changes = apply_tier2_results(chunk, result)
                    if changes and "ERROR" not in changes[0]:
                        stats["tier2_processed"] += 1
                    elif "ERROR" in (changes[0] if changes else ""):
                        stats["errors"] += 1
                    completed += 1
                    elapsed = time.time() - t0
                    rate = completed / elapsed if elapsed > 0 else 0
                    remaining = (total - completed) / rate if rate > 0 else 0
                    print(f"  [{completed}/{total}] {cid}: {', '.join(changes)} ({rate:.2f}/s, ~{remaining/60:.0f}m)", flush=True)
                except Exception as e:
                    print(f"  ERROR {cid}: {e}", flush=True)
                    stats["errors"] += 1

            await asyncio.gather(*[_process_t2(c) for c in tier2_chunks])

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
