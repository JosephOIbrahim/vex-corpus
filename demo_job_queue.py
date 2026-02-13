#!/usr/bin/env python3
"""Demonstrate job queue for bulk VEX corpus processing."""

import asyncio
import sys
import time
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).parent))

from ollama.src.client import OllamaClient
from ollama.src.queue import JobQueue, BatchProcessor


# Sample VEX snippets for bulk processing
VEX_SAMPLES = [
    {"id": "sample_001", "code": "@P += @N * ch('offset');"},
    {"id": "sample_002", "code": "@Cd = fit(@P.y, 0, 1, {0,0,1}, {1,0,0});"},
    {"id": "sample_003", "code": "int pts[] = primpoints(0, @primnum);"},
    {"id": "sample_004", "code": "setdetailattrib(0, 'count', npoints(0));"},
    {"id": "sample_005", "code": "@pscale = rand(@ptnum) * ch('scale');"},
    {"id": "sample_006", "code": "vector rest = point(1, 'rest', @ptnum); @P = lerp(@P, rest, ch('blend'));"},
    {"id": "sample_007", "code": "int h = pcopen(0, 'P', @P, 1.0, 10); @density = pcfilter(h, 'density'); pcclose(h);"},
    {"id": "sample_008", "code": "@N = normalize(cross(@P - point(0, 'P', (@ptnum+1) % @numpt), {0,1,0}));"},
]


async def demo_simple_queue():
    """Demonstrate basic job queue usage."""
    print("\n" + "=" * 70)
    print("DEMO 1: Simple Job Queue")
    print("=" * 70)

    client = OllamaClient()
    queue = JobQueue(client, max_concurrent=3, max_retries=1)

    # Add jobs
    print(f"\nAdding {len(VEX_SAMPLES)} jobs to queue...")
    for sample in VEX_SAMPLES:
        queue.add_job(
            "classify_wrangle",
            {"code": sample["code"]},
            job_id=sample["id"]
        )

    print(f"Queue stats: {queue.stats}")

    # Process with progress callback
    def on_progress(stats):
        print(f"\r  Progress: {stats.completed}/{stats.total_jobs} "
              f"(running: {stats.running}, pending: {stats.pending})", end="", flush=True)

    print("\nProcessing...")
    start = time.time()
    await queue.process_all(progress_callback=on_progress)
    elapsed = time.time() - start

    print(f"\n\nCompleted in {elapsed:.1f}s")
    print(f"Final stats: {queue.stats}")

    # Show results
    print("\nResults:")
    print("-" * 70)
    for result in queue.get_results():
        ctx = result.result.get("context", "?")
        print(f"  {result.task_id}: {ctx} (conf: {result.confidence:.2f})")

    # Show failed
    failed = queue.get_failed()
    if failed:
        print(f"\nFailed ({len(failed)}):")
        for job in failed:
            print(f"  {job.id}: {job.result.result.get('error', 'unknown error')}")

    return queue


async def demo_batch_add():
    """Demonstrate batch job addition."""
    print("\n" + "=" * 70)
    print("DEMO 2: Batch Job Addition")
    print("=" * 70)

    client = OllamaClient()
    queue = JobQueue(client, max_concurrent=5)

    # Add batch of jobs
    items = [{"id": s["id"], "code": s["code"]} for s in VEX_SAMPLES[:4]]
    jobs = queue.add_batch("classify_wrangle", items)

    print(f"Added {len(jobs)} jobs in batch")
    print(f"Queue has {queue.stats.pending} pending jobs")

    # Process
    print("\nProcessing batch...")
    start = time.time()
    await queue.process_all()
    elapsed = time.time() - start

    print(f"Completed in {elapsed:.1f}s")
    print(f"Throughput: {queue.stats.throughput_per_minute:.1f} jobs/min")

    return queue


async def demo_stream_processing():
    """Demonstrate streaming job processing."""
    print("\n" + "=" * 70)
    print("DEMO 3: Stream Processing (yield results as they complete)")
    print("=" * 70)

    client = OllamaClient()
    queue = JobQueue(client, max_concurrent=2)

    # Add jobs
    for sample in VEX_SAMPLES[:4]:
        queue.add_job("classify_wrangle", {"code": sample["code"]}, sample["id"])

    print(f"Processing {queue.stats.pending} jobs as stream...\n")

    # Process as stream
    async for job in queue.process_stream():
        ctx = job.result.result.get("context", "?") if job.result else "error"
        print(f"  Completed: {job.id} -> {ctx}")

    print("\nAll jobs streamed.")
    return queue


async def demo_export_results():
    """Demonstrate exporting results to JSON."""
    print("\n" + "=" * 70)
    print("DEMO 4: Export Results to JSON")
    print("=" * 70)

    client = OllamaClient()
    queue = JobQueue(client, max_concurrent=3)

    # Add and process jobs
    for sample in VEX_SAMPLES[:3]:
        queue.add_job("classify_wrangle", {"code": sample["code"]}, sample["id"])

    await queue.process_all()

    # Export
    output_path = Path("C:/Users/User/vex-corpus/output/results.json")
    output_path.parent.mkdir(exist_ok=True)
    queue.export_results(output_path)

    print(f"Exported {len(queue.get_results())} results to {output_path}")

    # Show file contents
    import json
    with open(output_path) as f:
        data = json.load(f)
    print(f"\nSample output:")
    print(json.dumps(data[0], indent=2))

    return queue


async def demo_multi_task_pipeline():
    """Demonstrate processing samples through multiple task types."""
    print("\n" + "=" * 70)
    print("DEMO 5: Multi-Task Pipeline")
    print("=" * 70)

    client = OllamaClient()

    # Create separate queues for different task types
    classify_queue = JobQueue(client, max_concurrent=3)
    extract_queue = JobQueue(client, max_concurrent=3)

    sample = VEX_SAMPLES[0]
    code = sample["code"]

    # Add same code to multiple task types
    classify_queue.add_job("classify_wrangle", {"code": code}, "classify")
    classify_queue.add_job("detect_bugs", {"code": code}, "bugs")
    extract_queue.add_job("extract_attributes", {"code": code}, "attrs")
    extract_queue.add_job("extract_functions", {"code": code}, "funcs")

    print(f"Sample: {code}")
    print("\nRunning 4 task types in parallel...")

    start = time.time()
    await asyncio.gather(
        classify_queue.process_all(),
        extract_queue.process_all(),
    )
    elapsed = time.time() - start

    print(f"\nCompleted in {elapsed:.1f}s")

    # Show results
    all_results = classify_queue.get_results() + extract_queue.get_results()
    print("\nResults:")
    for r in all_results:
        print(f"  {r.task_id}: {list(r.result.keys())[:3]}")

    return classify_queue, extract_queue


async def main():
    print("\n" + "#" * 70)
    print("#  JOB QUEUE DEMONSTRATION")
    print("#" * 70)

    client = OllamaClient()
    health = await client.health_check()
    print(f"\nOllama: {health['status']}")
    print(f"Models: {', '.join(health['available_models'])}")

    # Run demos
    await demo_simple_queue()
    await demo_batch_add()
    await demo_stream_processing()
    await demo_export_results()
    await demo_multi_task_pipeline()

    print("\n" + "#" * 70)
    print("#  JOB QUEUE DEMOS COMPLETE")
    print("#" * 70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
