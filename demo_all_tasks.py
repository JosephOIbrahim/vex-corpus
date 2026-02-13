#!/usr/bin/env python3
"""Demonstrate all 12 VEX processing task types with Ollama + Nemotron."""

import asyncio
import json
import sys
import time
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).parent))

from ollama.src.client import OllamaClient
from ollama.src.dispatcher import TaskDispatcher
from ollama.src.models import TaskType


# Sample VEX code for testing
SAMPLE_CODE = """// Point cloud relaxation with edge preservation
int handle = pcopen(0, "P", @P, ch("radius"), chi("maxpts"));
vector avg = pcfilter(handle, "P");
float density = pcfilter(handle, "density");
pcclose(handle);

// Preserve edges by checking normal similarity
vector avgN = pcfilter(handle, "N");
float edgeFactor = dot(normalize(@N), normalize(avgN));
edgeFactor = smooth(0.3, 0.7, edgeFactor);

// Apply relaxation with edge weighting
@P = lerp(@P, avg, ch("blend") * edgeFactor);
@Cd = fit(density, 0, 1, {0,0,1}, {1,0,0});
"""


async def run_task(dispatcher, task_type: str, input_data: dict, task_id: str):
    """Run a single task and return formatted result."""
    start = time.time()
    result = await dispatcher.dispatch(task_type, input_data, task_id)
    elapsed = time.time() - start

    return {
        "task": task_type,
        "elapsed": elapsed,
        "status": result.status.value,
        "confidence": result.confidence,
        "result": result.result,
        "model": result.model_used,
    }


async def demo_tier1_tasks(dispatcher):
    """Run all Tier 1 (classification) tasks."""
    print("\n" + "=" * 70)
    print("TIER 1: CLASSIFICATION TASKS (nemotron-mini 4B)")
    print("=" * 70)

    tasks = [
        ("classify_wrangle", {"code": SAMPLE_CODE}),
        ("extract_attributes", {"code": SAMPLE_CODE}),
        ("extract_functions", {"code": SAMPLE_CODE}),
        ("detect_bugs", {"code": SAMPLE_CODE}),
        ("estimate_complexity", {"code": SAMPLE_CODE}),
        ("classify_topic", {"code": SAMPLE_CODE}),
    ]

    results = []
    for task_type, input_data in tasks:
        print(f"\n[{task_type}] Running...")
        result = await run_task(dispatcher, task_type, input_data, f"demo_{task_type}")
        results.append(result)

        print(f"  Status: {result['status']} ({result['elapsed']:.2f}s)")
        print(f"  Confidence: {result['confidence']}")

        # Show key result fields
        r = result['result']
        if task_type == "classify_wrangle":
            print(f"  Context: {r.get('context', 'unknown')}")
        elif task_type == "extract_attributes":
            reads = r.get('reads', [])
            writes = r.get('writes', [])
            print(f"  Reads: {len(reads)}, Writes: {len(writes)}")
        elif task_type == "extract_functions":
            funcs = r.get('functions', [])
            print(f"  Functions: {len(funcs)}")
        elif task_type == "detect_bugs":
            bugs = r.get('bugs', [])
            print(f"  Bugs: {len(bugs)}, Quality: {r.get('overall_quality', '?')}")
        elif task_type == "estimate_complexity":
            print(f"  Complexity: {r.get('complexity', '?')}")
        elif task_type == "classify_topic":
            print(f"  Topic: {r.get('primary_topic', '?')}")

    return results


async def demo_tier2_tasks(dispatcher, client):
    """Run all Tier 2 (generation) tasks."""
    print("\n" + "=" * 70)
    print("TIER 2: GENERATION TASKS (nemotron 70B / fallback)")
    print("=" * 70)

    results = []

    # generate_prompt
    print("\n[generate_prompt] Running...")
    result = await run_task(
        dispatcher,
        "generate_prompt",
        {"code": "@P += @N * ch('offset');", "task": "Normal displacement", "style": "imperative"},
        "demo_gen_prompt"
    )
    results.append(result)
    print(f"  Status: {result['status']} ({result['elapsed']:.2f}s)")
    prompt = result['result'].get('prompt', '')[:80]
    print(f"  Prompt: {prompt}...")

    # generate_explanation
    print("\n[generate_explanation] Running...")
    result = await run_task(
        dispatcher,
        "generate_explanation",
        {"code": SAMPLE_CODE[:200], "task": "Point relaxation", "audience": "intermediate"},
        "demo_gen_explain"
    )
    results.append(result)
    print(f"  Status: {result['status']} ({result['elapsed']:.2f}s)")
    explanation = result['result'].get('explanation', '')[:100]
    print(f"  Explanation: {explanation}...")

    # inject_bug
    print("\n[inject_bug] Running...")
    clean_code = """int handle = pcopen(0, "P", @P, 1.0, 10);
vector avg = pcfilter(handle, "P");
pcclose(handle);
@P = avg;"""
    result = await run_task(
        dispatcher,
        "inject_bug",
        {"code": clean_code, "bug_type": "resource_leak"},
        "demo_inject_bug"
    )
    results.append(result)
    print(f"  Status: {result['status']} ({result['elapsed']:.2f}s)")
    print(f"  Bug injected: {result['result'].get('bug_description', '?')[:60]}...")

    # paraphrase_prompt
    print("\n[paraphrase_prompt] Running...")
    result = await run_task(
        dispatcher,
        "paraphrase_prompt",
        {"prompt": "Write VEX code to displace points along their normals"},
        "demo_paraphrase"
    )
    results.append(result)
    print(f"  Status: {result['status']} ({result['elapsed']:.2f}s)")

    # rate_difficulty
    print("\n[rate_difficulty] Running...")
    result = await run_task(
        dispatcher,
        "rate_difficulty",
        {"code": SAMPLE_CODE, "task": "Edge-preserving relaxation"},
        "demo_rate"
    )
    results.append(result)
    print(f"  Status: {result['status']} ({result['elapsed']:.2f}s)")
    print(f"  Difficulty: {result['result'].get('difficulty', '?')}/10")

    # infer_context
    print("\n[infer_context] Running...")
    result = await run_task(
        dispatcher,
        "infer_context",
        {"code": SAMPLE_CODE},
        "demo_infer"
    )
    results.append(result)
    print(f"  Status: {result['status']} ({result['elapsed']:.2f}s)")
    print(f"  Wrangle type: {result['result'].get('wrangle_type', '?')}")

    return results


async def main():
    print("\n" + "#" * 70)
    print("#  ALL 12 VEX TASK TYPES - OLLAMA + NEMOTRON DEMO")
    print("#" * 70)

    client = OllamaClient()
    dispatcher = TaskDispatcher(client=client)

    health = await client.health_check()
    print(f"\nOllama Status: {health['status']}")
    print(f"Models: {', '.join(health['available_models'])}")

    print("\n" + "-" * 70)
    print("Sample VEX Code:")
    print("-" * 70)
    for line in SAMPLE_CODE.strip().split('\n')[:8]:
        print(f"  {line}")
    print("  ...")

    # Run all tasks
    tier1_results = await demo_tier1_tasks(dispatcher)
    tier2_results = await demo_tier2_tasks(dispatcher, client)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    all_results = tier1_results + tier2_results
    success_count = sum(1 for r in all_results if r['status'] == 'success')
    total_time = sum(r['elapsed'] for r in all_results)
    avg_confidence = sum(r['confidence'] for r in all_results) / len(all_results)

    print(f"\nTotal tasks: {len(all_results)}")
    print(f"Successful: {success_count}/{len(all_results)}")
    print(f"Total time: {total_time:.1f}s")
    print(f"Average confidence: {avg_confidence:.2f}")

    print("\n" + "#" * 70)
    print("#  DEMO COMPLETE")
    print("#" * 70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
