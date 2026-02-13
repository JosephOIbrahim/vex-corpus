#!/usr/bin/env python3
"""Quick start demonstration of VEX corpus processing with Ollama + Nemotron."""

import asyncio
import json
import sys
import time
from pathlib import Path

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).parent))

from ollama.src.client import OllamaClient
from ollama.src.dispatcher import TaskDispatcher


def safe_print(text):
    """Print with safe encoding for Windows."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode('ascii', 'replace').decode())


async def demo_single_task():
    """Demonstrate single task processing."""
    safe_print("\n" + "=" * 70)
    safe_print("DEMO 1: Single Task Processing")
    safe_print("=" * 70)

    client = OllamaClient()
    dispatcher = TaskDispatcher(client=client)

    code = "@P += curlnoise(@P * ch('freq')) * ch('amp');"
    safe_print(f"\nInput VEX: {code}")
    safe_print("\nProcessing with nemotron-mini (Tier 1)...")

    start = time.time()
    result = await dispatcher.dispatch(
        "classify_wrangle",
        {"code": code},
        task_id="demo_001"
    )
    elapsed = time.time() - start

    safe_print(f"\nResult ({elapsed:.2f}s):")
    safe_print(f"  Context: {result.result.get('context')}")
    safe_print(f"  Confidence: {result.confidence}")
    safe_print(f"  Model: {result.model_used}")

    return result


async def demo_batch_processing():
    """Demonstrate batch processing with concurrency."""
    safe_print("\n" + "=" * 70)
    safe_print("DEMO 2: Batch Processing (5 samples)")
    safe_print("=" * 70)

    client = OllamaClient()
    dispatcher = TaskDispatcher(client=client)

    samples = [
        {"id": "batch_001", "code": "@P += @N * ch('offset');"},
        {"id": "batch_002", "code": "@Cd = fit(@P.y, 0, 1, {0,0,1}, {1,0,0});"},
        {"id": "batch_003", "code": "int pts[] = primpoints(0, @primnum);"},
        {"id": "batch_004", "code": "setdetailattrib(0, 'total', npoints(0));"},
        {"id": "batch_005", "code": "@pscale = rand(@ptnum);"},
    ]

    safe_print(f"\nProcessing {len(samples)} samples with concurrency=3...")
    start = time.time()

    batch_result = await dispatcher.dispatch_batch(
        "classify_wrangle",
        samples,
        concurrency=3
    )

    elapsed = time.time() - start

    safe_print(f"\nResults ({elapsed:.2f}s total, {elapsed/len(samples):.2f}s avg):")
    safe_print("-" * 70)
    safe_print(f"{'ID':<12} {'Context':<10} {'Confidence':<12} {'Status'}")
    safe_print("-" * 70)

    for r in batch_result.results:
        ctx = r.result.get("context", "?")
        safe_print(f"{r.task_id:<12} {ctx:<10} {r.confidence:<12.2f} {r.status.value}")

    safe_print("-" * 70)
    stats = batch_result.batch_stats
    safe_print(f"Success: {stats['success']}/{stats['total']}, Avg Confidence: {stats['avg_confidence']:.2f}")

    return batch_result


async def demo_parallel_tasks():
    """Demonstrate multiple task types running in parallel."""
    safe_print("\n" + "=" * 70)
    safe_print("DEMO 3: Parallel Multi-Task Processing")
    safe_print("=" * 70)

    client = OllamaClient()
    dispatcher = TaskDispatcher(client=client)

    code = """float density = point(1, "density", @ptnum);
@Cd = fit(density, 0, 1, {0,0,1}, {1,0,0});
@pscale = ch("scale") * density;
if(density < 0.1) removepoint(0, @ptnum);"""

    safe_print(f"\nInput VEX:")
    for line in code.split('\n'):
        safe_print(f"  {line}")

    safe_print("\nRunning classify + extract_attributes + detect_bugs in parallel...")
    start = time.time()

    results = await asyncio.gather(
        dispatcher.dispatch("classify_wrangle", {"code": code}, "task_classify"),
        dispatcher.dispatch("extract_attributes", {"code": code}, "task_attrs"),
        dispatcher.dispatch("detect_bugs", {"code": code}, "task_bugs"),
    )

    elapsed = time.time() - start
    safe_print(f"\nCompleted in {elapsed:.2f}s (parallel execution)")

    # Classify result
    r = results[0]
    safe_print(f"\n[classify_wrangle]")
    safe_print(f"  Context: {r.result.get('context', 'unknown')}")
    safe_print(f"  Confidence: {r.confidence}")

    # Attributes result
    r = results[1]
    safe_print(f"\n[extract_attributes]")
    reads = r.result.get("reads", [])
    writes = r.result.get("writes", [])
    channels = r.result.get("channels", [])

    read_names = [a.get('name') if isinstance(a, dict) else str(a) for a in reads[:5]]
    write_names = [a.get('name') if isinstance(a, dict) else str(a) for a in writes[:5]]
    channel_names = [c.get('name') if isinstance(c, dict) else str(c) for c in channels[:5]]

    safe_print(f"  Reads: {read_names}")
    safe_print(f"  Writes: {write_names}")
    safe_print(f"  Channels: {channel_names}")

    # Bugs result
    r = results[2]
    safe_print(f"\n[detect_bugs]")
    bugs = r.result.get("bugs", [])
    quality = r.result.get("overall_quality", 'unknown')
    safe_print(f"  Quality: {quality}")
    safe_print(f"  Bugs: {len(bugs)}")

    return results


async def demo_tier2_generation():
    """Demonstrate Tier 2 generation with nemotron 70B."""
    safe_print("\n" + "=" * 70)
    safe_print("DEMO 4: Tier 2 Generation (nemotron 70B)")
    safe_print("=" * 70)

    client = OllamaClient()

    code = """int handle = pcopen(0, "P", @P, ch("radius"), chi("maxpts"));
vector avg = pcfilter(handle, "P");
pcclose(handle);
@P = lerp(@P, avg, ch("blend"));"""

    safe_print(f"\nInput VEX (Point Cloud Relaxation):")
    for line in code.split('\n'):
        safe_print(f"  {line}")

    safe_print("\nGenerating explanation with nemotron 70B (this takes ~30s)...")
    start = time.time()

    result = await client.process_task(
        task_type="generate_explanation",
        input_data={
            "code": code,
            "task": "Point cloud relaxation",
            "audience": "intermediate"
        },
        task_id="gen_001"
    )

    elapsed = time.time() - start

    safe_print(f"\nGenerated in {elapsed:.2f}s:")
    safe_print("-" * 70)

    explanation = result.result.get("explanation", "")
    if explanation:
        # Wrap long lines
        words = explanation.split()
        line = ""
        for word in words:
            if len(line) + len(word) > 68:
                safe_print(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        if line:
            safe_print(line)
    else:
        safe_print(json.dumps(result.result, indent=2, ensure_ascii=True))

    safe_print("-" * 70)

    concepts = result.result.get("key_concepts", [])
    if concepts:
        safe_print(f"Key concepts: {concepts[:5]}")

    safe_print(f"Model: {result.model_used}")
    safe_print(f"Confidence: {result.confidence}")

    return result


async def demo_review_queue():
    """Demonstrate review queue for low-confidence results."""
    safe_print("\n" + "=" * 70)
    safe_print("DEMO 5: Review Queue (Quality Filtering)")
    safe_print("=" * 70)

    client = OllamaClient()
    dispatcher = TaskDispatcher(client=client)

    samples = [
        {"id": "clear_001", "code": "@P += @N * 0.1;"},
        {"id": "clear_002", "code": "setdetailattrib(0, 'count', npoints(0));"},
        {"id": "ambig_001", "code": "int x = 5;"},
        {"id": "clear_003", "code": "float a = prim(0, 'area', @primnum);"},
    ]

    safe_print(f"\nProcessing {len(samples)} samples with quality filtering...")

    passed, flagged = await dispatcher.process_with_review(
        "classify_wrangle",
        samples,
        concurrency=3
    )

    safe_print(f"\nPassed Quality Check ({len(passed)}):")
    for r in passed:
        safe_print(f"  {r.task_id}: {r.result.get('context', '?')} (conf: {r.confidence:.2f})")

    safe_print(f"\nFlagged for Human Review ({len(flagged)}):")
    for r in flagged:
        safe_print(f"  {r.task_id}: {r.result.get('context', '?')} (conf: {r.confidence:.2f})")

    return passed, flagged


async def main():
    """Run all demos."""
    safe_print("\n" + "#" * 70)
    safe_print("#  VEX CORPUS + OLLAMA + NEMOTRON INTEGRATION DEMO")
    safe_print("#" * 70)

    client = OllamaClient()
    health = await client.health_check()
    safe_print(f"\nOllama Status: {health['status']}")
    safe_print(f"Available Models: {', '.join(health['available_models'])}")

    if health['status'] == 'offline':
        safe_print("\nERROR: Ollama is not running. Start with: ollama serve")
        return

    # Run Tier 1 demos (fast)
    await demo_single_task()
    await demo_batch_processing()
    await demo_parallel_tasks()
    await demo_review_queue()

    # Run Tier 2 demo (slower)
    await demo_tier2_generation()

    safe_print("\n" + "#" * 70)
    safe_print("#  ALL DEMOS COMPLETE")
    safe_print("#" * 70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
