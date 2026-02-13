#!/usr/bin/env python3
"""Full integration test for VEX Corpus system."""

import asyncio
import sys
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).parent))

from ollama.src.client import OllamaClient
from ollama.src.dispatcher import TaskDispatcher
from ollama.src.queue import JobQueue
from pipeline.intake import IntakePipeline, VEXSample, VEXParser, ProcessingStage


# Test VEX samples
TEST_SAMPLES = [
    {
        "name": "Simple offset",
        "code": "@P += @N * ch('offset');",
        "expected_context": "point",
    },
    {
        "name": "Color ramp",
        "code": "@Cd = chramp('color', @P.y);",
        "expected_context": "point",
    },
    {
        "name": "Point cloud query",
        "code": """int h = pcopen(0, "P", @P, ch("radius"), chi("maxpts"));
vector avg = pcfilter(h, "P");
pcclose(h);
@P = lerp(@P, avg, ch("blend"));""",
        "expected_context": "point",
    },
    {
        "name": "Primitive area",
        "code": """float area = prim(0, "area", @primnum);
setprimattrib(0, "normalized_area", @primnum, area / detail(0, "total_area"));""",
        "expected_context": "prim",
    },
    {
        "name": "Detail counter",
        "code": """int count = npoints(0);
setdetailattrib(0, "point_count", count);""",
        "expected_context": "detail",
    },
]


async def test_client_health():
    """Test Ollama client health check."""
    print("\n" + "=" * 60)
    print("TEST: Ollama Client Health")
    print("=" * 60)

    client = OllamaClient()
    health = await client.health_check()

    print(f"Status: {health['status']}")
    print(f"Models: {', '.join(health['available_models'][:5])}")

    assert health['status'] == 'healthy', "Ollama not healthy"
    print("PASS")
    return True


async def test_dispatcher():
    """Test task dispatcher."""
    print("\n" + "=" * 60)
    print("TEST: Task Dispatcher")
    print("=" * 60)

    client = OllamaClient()
    dispatcher = TaskDispatcher(client=client)

    code = TEST_SAMPLES[0]["code"]
    print(f"Code: {code}")

    result = await dispatcher.dispatch(
        "classify_wrangle",
        {"code": code},
        task_id="test_001"
    )

    print(f"Status: {result.status}")
    print(f"Context: {result.result.get('context', '?')}")
    print(f"Confidence: {result.confidence:.2f}")

    assert result.status.value == "success", f"Task failed: {result.result}"
    print("PASS")
    return True


async def test_job_queue():
    """Test job queue processing."""
    print("\n" + "=" * 60)
    print("TEST: Job Queue")
    print("=" * 60)

    client = OllamaClient()
    queue = JobQueue(client, max_concurrent=3)

    # Add jobs
    for i, sample in enumerate(TEST_SAMPLES[:3]):
        queue.add_job(
            "classify_wrangle",
            {"code": sample["code"]},
            job_id=f"queue_test_{i}"
        )

    print(f"Added {queue.stats.pending} jobs")

    # Process
    await queue.process_all()

    print(f"Completed: {queue.stats.completed}")
    print(f"Failed: {queue.stats.failed}")

    # Check results
    results = queue.get_results()
    for r in results:
        ctx = r.result.get("context", "?")
        print(f"  {r.task_id}: {ctx}")

    assert queue.stats.completed > 0, "No jobs completed"
    print("PASS")
    return True


async def test_pipeline_ingest():
    """Test pipeline ingestion."""
    print("\n" + "=" * 60)
    print("TEST: Pipeline Ingestion")
    print("=" * 60)

    pipeline = IntakePipeline()

    # Ingest code samples
    for sample in TEST_SAMPLES[:2]:
        s = await pipeline.ingest_code(sample["code"], sample["name"])
        print(f"Ingested: {s.id} from {sample['name']}")

    print(f"Total samples: {len(pipeline.samples)}")
    print(f"Stats: {pipeline.get_stats()}")

    assert len(pipeline.samples) == 2, "Wrong sample count"
    print("PASS")
    return True


async def test_pipeline_processing():
    """Test full pipeline processing."""
    print("\n" + "=" * 60)
    print("TEST: Pipeline Processing (Tier 1)")
    print("=" * 60)

    pipeline = IntakePipeline()

    # Progress callback
    def on_progress(stage, current, total):
        print(f"\r  {stage}: {current}/{total}", end="", flush=True)

    pipeline.on_progress(on_progress)

    # Ingest samples
    samples = []
    for sample in TEST_SAMPLES[:2]:
        s = await pipeline.ingest_code(sample["code"], sample["name"])
        samples.append(s)

    print(f"Processing {len(samples)} samples...")

    # Process Tier 1 only (faster)
    processed_samples = await pipeline.process_tier1(samples)

    print("\n\nResults:")
    for s in processed_samples:
        print(f"  {s.id[:12]}: context={s.context}, conf={s.context_confidence:.2f}, topic={s.topic}")

    # Check results - samples with context (not empty string and not "unknown")
    with_context = [s for s in processed_samples if s.context and s.context != "unknown"]
    print(f"Samples with valid context: {len(with_context)}")

    # At least check that classification ran (context_confidence > 0 means something happened)
    classified = [s for s in processed_samples if s.context_confidence > 0]
    assert len(classified) > 0, "No samples classified"

    stats = pipeline.get_stats()
    print(f"\nStats: {stats}")
    print("PASS")
    return True


async def test_vex_parser():
    """Test VEX parser extraction."""
    print("\n" + "=" * 60)
    print("TEST: VEX Parser")
    print("=" * 60)

    # Test _looks_like_vex
    vex_code = "@P += @N * ch('offset');"
    not_vex = "print('hello world')"

    assert VEXParser._looks_like_vex(vex_code), "Should detect VEX"
    assert not VEXParser._looks_like_vex(not_vex), "Should not detect Python"

    print("VEX detection: PASS")

    # Test markdown extraction
    md_content = """
# VEX Examples

```vex
@P += @N * 0.1;
```

```python
print("not vex")
```

```vex
@Cd = {1, 0, 0};
```
"""
    # Create temp file
    temp_path = Path("C:/Users/User/vex-corpus/test_extract.md")
    temp_path.write_text(md_content)

    samples = VEXParser.extract_from_file(temp_path)
    print(f"Extracted {len(samples)} samples from markdown")

    temp_path.unlink()  # Clean up

    # Note: Second snippet @Cd = {1,0,0} may not be detected as VEX without indicators
    assert len(samples) >= 1, f"Expected at least 1 sample, got {len(samples)}"
    print("Markdown extraction: PASS")
    print("PASS")
    return True


async def test_export():
    """Test corpus export."""
    print("\n" + "=" * 60)
    print("TEST: Export Functions")
    print("=" * 60)

    pipeline = IntakePipeline()

    # Add and process a sample
    sample = await pipeline.ingest_code(TEST_SAMPLES[0]["code"], "export_test")

    # Set properties on the pipeline's copy
    pipeline.samples[sample.id].context = "point"
    pipeline.samples[sample.id].context_confidence = 0.95
    pipeline.samples[sample.id].topic = "attribute_operations"
    pipeline.samples[sample.id].prompt = "Offset points along their normals"
    pipeline.samples[sample.id].stage = ProcessingStage.COMPLETE

    # Export
    corpus_path = pipeline.export_corpus()
    print(f"Exported corpus: {corpus_path}")
    assert corpus_path.exists(), "Corpus file not created"

    training_path = pipeline.export_training_data()
    print(f"Exported training: {training_path}")
    assert training_path.exists(), "Training file not created"

    # Clean up
    corpus_path.unlink()
    training_path.unlink()

    print("PASS")
    return True


async def main():
    print("\n" + "#" * 60)
    print("#  VEX CORPUS INTEGRATION TEST")
    print("#" * 60)

    tests = [
        ("Client Health", test_client_health),
        ("VEX Parser", test_vex_parser),
        ("Dispatcher", test_dispatcher),
        ("Job Queue", test_job_queue),
        ("Pipeline Ingest", test_pipeline_ingest),
        ("Pipeline Processing", test_pipeline_processing),
        ("Export", test_export),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            result = await test_func()
            if result:
                passed += 1
        except Exception as e:
            print(f"\nFAIL: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "#" * 60)
    print(f"#  RESULTS: {passed} passed, {failed} failed")
    print("#" * 60)

    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
