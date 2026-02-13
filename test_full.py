#!/usr/bin/env python3
"""Comprehensive test of VEX corpus processing with model comparison."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from ollama.src.client import OllamaClient

# Test samples with known correct answers
TEST_SAMPLES = [
    {
        "id": "point_001",
        "code": '@P += curlnoise(@P * ch("freq")) * ch("amp");',
        "expected_context": "point",
        "description": "Curl noise displacement",
    },
    {
        "id": "point_002",
        "code": "@Cd = fit(@P.y, 0, 1, {0,0,1}, {1,0,0});",
        "expected_context": "point",
        "description": "Color gradient by height",
    },
    {
        "id": "prim_001",
        "code": '''int pts[] = primpoints(0, @primnum);
vector centroid = {0,0,0};
foreach(int pt; pts) centroid += point(0, "P", pt);
v@centroid = centroid / len(pts);''',
        "expected_context": "prim",
        "description": "Primitive centroid calculation",
    },
    {
        "id": "prim_002",
        "code": "float area = prim(0, 'area', @primnum); @Cd = fit(area, 0, 1, {1,1,1}, {1,0,0});",
        "expected_context": "prim",
        "description": "Color by primitive area",
    },
    {
        "id": "detail_001",
        "code": "int total = npoints(0); setdetailattrib(0, 'point_count', total);",
        "expected_context": "detail",
        "description": "Count points to detail",
    },
    {
        "id": "pcopen_001",
        "code": '''int handle = pcopen(0, "P", @P, ch("radius"), chi("maxpts"));
vector avg = pcfilter(handle, "P");
pcclose(handle);
@P = lerp(@P, avg, ch("blend"));''',
        "expected_context": "point",
        "description": "Point cloud relaxation",
    },
]


async def test_model(client: OllamaClient, model: str, sample: dict) -> dict:
    """Test a single sample with a specific model."""
    result = await client.process_task(
        task_type="classify_wrangle",
        input_data={"code": sample["code"]},
        task_id=sample["id"],
        model=model,
    )

    detected = result.result.get("context", "unknown")
    expected = sample["expected_context"]
    correct = detected == expected

    return {
        "id": sample["id"],
        "description": sample["description"],
        "expected": expected,
        "detected": detected,
        "correct": correct,
        "confidence": result.confidence or result.result.get("confidence", 0),
        "status": result.status.value,
    }


async def main():
    print("\n" + "=" * 70)
    print("VEX Corpus - Model Comparison Test")
    print("=" * 70)

    client = OllamaClient()

    # Health check
    health = await client.health_check()
    print(f"\nOllama Status: {health['status']}")
    print(f"Available Models: {', '.join(health['available_models'])}")

    # Models to test
    models = ["nemotron-mini:latest", "nemotron:latest"]

    for model in models:
        print(f"\n{'=' * 70}")
        print(f"Testing: {model}")
        print("=" * 70)
        print(f"{'ID':<12} {'Description':<32} {'Expected':<8} {'Detected':<10} {'OK?':<4} {'Conf':<6}")
        print("-" * 70)

        correct_count = 0
        for sample in TEST_SAMPLES:
            try:
                result = await test_model(client, model, sample)
                correct_str = "YES" if result["correct"] else "NO"
                if result["correct"]:
                    correct_count += 1

                print(
                    f"{result['id']:<12} "
                    f"{result['description'][:30]:<32} "
                    f"{result['expected']:<8} "
                    f"{result['detected']:<10} "
                    f"{correct_str:<4} "
                    f"{result['confidence']:.2f}"
                )
            except Exception as e:
                print(
                    f"{sample['id']:<12} "
                    f"{sample['description'][:30]:<32} "
                    f"{sample['expected_context']:<8} "
                    f"{'ERROR':<10} "
                    f"{'NO':<4} "
                    f"{str(e)[:20]}"
                )

        print("-" * 70)
        print(f"Accuracy: {correct_count}/{len(TEST_SAMPLES)} ({100*correct_count/len(TEST_SAMPLES):.1f}%)")


if __name__ == "__main__":
    asyncio.run(main())
