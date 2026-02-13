#!/usr/bin/env python3
"""Benchmark Nemotron models to find optimal Tier 2 choice."""

import asyncio
import sys
import time
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).parent))

import httpx


# Test prompts - one simple, one complex
SIMPLE_CODE = '@P += @N * ch("offset");'

COMPLEX_CODE = """int handle = pcopen(0, "P", @P, ch("radius"), chi("maxpts"));
vector avg = pcfilter(handle, "P");
float density = pcfilter(handle, "density");
pcclose(handle);
vector avgN = pcfilter(handle, "N");
float edgeFactor = dot(normalize(@N), normalize(avgN));
@P = lerp(@P, avg, ch("blend") * smooth(0.3, 0.7, edgeFactor));
@Cd = fit(density, 0, 1, {0,0,1}, {1,0,0});"""

SYSTEM_PROMPT = """You are a VEX code analyzer. Respond with ONLY valid JSON:
{
  "task_id": "<id>",
  "status": "success",
  "result": {
    "context": "point|prim|vertex|detail|unknown",
    "confidence": 0.0-1.0,
    "evidence": ["indicators"]
  },
  "confidence": 0.0-1.0
}"""


async def benchmark_model(model: str, code: str, task_name: str) -> dict:
    """Benchmark a single model on a task."""
    prompt = f"Task ID: {task_name}\n\nVEX Code:\n```vex\n{code}\n```\n\nAnalyze and respond with JSON."

    start = time.time()

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "system": SYSTEM_PROMPT,
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 512}
                },
                timeout=180.0,
            )
            response.raise_for_status()
            data = response.json()

            elapsed = time.time() - start
            tokens = data.get("eval_count", 0)

            # Check if response is valid JSON
            raw = data.get("response", "")
            valid_json = "{" in raw and "}" in raw

            # Extract context if possible
            context = "?"
            if '"context"' in raw:
                try:
                    import json
                    # Try to parse
                    content = raw
                    if "```" in content:
                        content = content.split("```")[1].split("```")[0]
                    parsed = json.loads(content.strip())
                    context = parsed.get("result", {}).get("context", "?")
                except:
                    pass

            return {
                "model": model,
                "task": task_name,
                "time": elapsed,
                "tokens": tokens,
                "tokens_per_sec": tokens / elapsed if elapsed > 0 else 0,
                "valid_json": valid_json,
                "context": context,
                "success": True,
            }

        except httpx.TimeoutException:
            return {
                "model": model,
                "task": task_name,
                "time": 180.0,
                "tokens": 0,
                "tokens_per_sec": 0,
                "valid_json": False,
                "context": "TIMEOUT",
                "success": False,
            }
        except Exception as e:
            return {
                "model": model,
                "task": task_name,
                "time": time.time() - start,
                "tokens": 0,
                "tokens_per_sec": 0,
                "valid_json": False,
                "context": f"ERROR: {str(e)[:20]}",
                "success": False,
            }


async def main():
    print("\n" + "#" * 70)
    print("#  NEMOTRON MODEL BENCHMARK")
    print("#" * 70)

    # Models to test (ordered by size)
    models = [
        "nemotron-mini:latest",      # 4.2B - Tier 1
        "nemotron-3-nano:latest",    # 31.6B - Candidate for Tier 2
        "nemotron:latest",           # 70.6B - Current Tier 2
    ]

    tasks = [
        ("simple", SIMPLE_CODE),
        ("complex", COMPLEX_CODE),
    ]

    results = []

    for model in models:
        print(f"\n{'=' * 70}")
        print(f"Testing: {model}")
        print("=" * 70)

        for task_name, code in tasks:
            print(f"\n  [{task_name}] Running...", end=" ", flush=True)
            result = await benchmark_model(model, code, task_name)
            results.append(result)

            status = "OK" if result["success"] else "FAIL"
            print(f"{status} - {result['time']:.1f}s, {result['tokens_per_sec']:.1f} tok/s, context={result['context']}")

    # Summary table
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"\n{'Model':<25} {'Task':<10} {'Time':>8} {'Tok/s':>8} {'Context':<10} {'Status'}")
    print("-" * 70)

    for r in results:
        model_short = r["model"].replace(":latest", "")
        status = "OK" if r["success"] else "FAIL"
        print(f"{model_short:<25} {r['task']:<10} {r['time']:>7.1f}s {r['tokens_per_sec']:>7.1f} {r['context']:<10} {status}")

    # Recommendations
    print("\n" + "=" * 70)
    print("RECOMMENDATIONS")
    print("=" * 70)

    # Group by model
    by_model = {}
    for r in results:
        model = r["model"]
        if model not in by_model:
            by_model[model] = []
        by_model[model].append(r)

    print("\n  Model Performance Summary:")
    for model, model_results in by_model.items():
        avg_time = sum(r["time"] for r in model_results) / len(model_results)
        avg_tps = sum(r["tokens_per_sec"] for r in model_results) / len(model_results)
        success_rate = sum(1 for r in model_results if r["success"]) / len(model_results)

        model_short = model.replace(":latest", "")
        print(f"    {model_short:<25} avg={avg_time:.1f}s  {avg_tps:.1f} tok/s  success={success_rate*100:.0f}%")

    print("\n" + "#" * 70)
    print("#  BENCHMARK COMPLETE")
    print("#" * 70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
