#!/usr/bin/env python3
"""Test script for VEX corpus classify_wrangle task."""

import asyncio
import json
import sys
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from ollama.src.client import OllamaClient
from ollama.src.dispatcher import TaskDispatcher


async def main():
    print("=" * 60)
    print("VEX Corpus - Ollama Integration Test")
    print("=" * 60)

    # Initialize client
    client = OllamaClient()

    # Health check
    print("\n[1] Health Check...")
    health = await client.health_check()
    print(f"    Status: {health['status']}")
    print(f"    Models: {', '.join(health['available_models'])}")
    if health.get("missing_models"):
        print(f"    Missing: {', '.join(health['missing_models'])}")

    # Load blueprint
    print("\n[2] Loading Blueprint...")
    blueprint = client.load_blueprint()
    print(f"    Blueprint loaded: {len(blueprint):,} characters")

    # Test VEX sample
    test_code = '@P += curlnoise(@P * ch("freq")) * ch("amp");'

    print("\n[3] Testing classify_wrangle...")
    print(f"    Input: {test_code}")

    dispatcher = TaskDispatcher(client=client)
    result = await dispatcher.dispatch(
        task_type="classify_wrangle",
        input_data={"code": test_code},
        task_id="test_001",
    )

    print(f"\n[4] Result:")
    print(f"    Status: {result.status.value}")
    print(f"    Model: {result.model_used}")
    print(f"    Confidence: {result.confidence}")
    print(f"    Result: {json.dumps(result.result, indent=2)}")

    if result.processing_notes:
        print(f"    Notes: {result.processing_notes}")

    # Test a second sample (primitive context)
    test_code_2 = """int pts[] = primpoints(0, @primnum);
vector centroid = {0,0,0};
foreach(int pt; pts) centroid += point(0, "P", pt);
@P = centroid / len(pts);"""

    print("\n[5] Testing with primitive-context VEX...")
    print(f"    Input: primpoints + @primnum pattern")

    result2 = await dispatcher.dispatch(
        task_type="classify_wrangle",
        input_data={"code": test_code_2},
        task_id="test_002",
    )

    print(f"\n[6] Result:")
    print(f"    Status: {result2.status.value}")
    print(f"    Context: {result2.result.get('context', 'N/A')}")
    print(f"    Confidence: {result2.confidence}")

    print("\n" + "=" * 60)
    print("Test Complete!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
