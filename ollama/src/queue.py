"""Job queue for bulk VEX corpus processing with Ollama."""

import asyncio
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable
from collections import deque

from .client import OllamaClient
from .dispatcher import TaskDispatcher
from .models import TaskResult, TaskStatus, TaskType


class JobStatus(str, Enum):
    """Status of a processing job."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    """A single processing job."""
    id: str
    task_type: str
    input_data: dict[str, Any]
    status: JobStatus = JobStatus.PENDING
    result: TaskResult | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    retries: int = 0
    max_retries: int = 2
    priority: int = 0  # Higher = more urgent


@dataclass
class QueueStats:
    """Statistics for the job queue."""
    total_jobs: int = 0
    pending: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    avg_processing_time: float = 0.0
    throughput_per_minute: float = 0.0


class JobQueue:
    """Async job queue for bulk VEX processing."""

    def __init__(
        self,
        client: OllamaClient | None = None,
        max_concurrent: int = 5,
        max_retries: int = 2,
    ):
        self.client = client or OllamaClient()
        self.dispatcher = TaskDispatcher(client=self.client)
        self.max_concurrent = max_concurrent
        self.max_retries = max_retries

        self._queue: deque[Job] = deque()
        self._running: dict[str, Job] = {}
        self._completed: list[Job] = []
        self._failed: list[Job] = []

        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._running_flag = False
        self._processing_times: list[float] = []
        self._callbacks: list[Callable[[Job], None]] = []

    def add_job(
        self,
        task_type: str,
        input_data: dict[str, Any],
        job_id: str | None = None,
        priority: int = 0,
    ) -> Job:
        """Add a job to the queue."""
        job = Job(
            id=job_id or f"job_{len(self._queue) + len(self._completed) + 1:06d}",
            task_type=task_type,
            input_data=input_data,
            max_retries=self.max_retries,
            priority=priority,
        )
        self._queue.append(job)
        return job

    def add_batch(
        self,
        task_type: str,
        items: list[dict[str, Any]],
        priority: int = 0,
    ) -> list[Job]:
        """Add multiple jobs to the queue."""
        jobs = []
        for item in items:
            job_id = item.pop("id", None)
            job = self.add_job(task_type, item, job_id=job_id, priority=priority)
            jobs.append(job)
        return jobs

    def on_complete(self, callback: Callable[[Job], None]):
        """Register a callback for job completion."""
        self._callbacks.append(callback)

    async def _process_job(self, job: Job):
        """Process a single job."""
        async with self._semaphore:
            job.status = JobStatus.RUNNING
            job.started_at = time.time()
            self._running[job.id] = job

            try:
                result = await self.dispatcher.dispatch(
                    job.task_type,
                    job.input_data,
                    task_id=job.id,
                )

                job.result = result
                job.completed_at = time.time()

                if result.status == TaskStatus.ERROR and job.retries < job.max_retries:
                    job.retries += 1
                    job.status = JobStatus.PENDING
                    self._queue.append(job)
                elif result.status == TaskStatus.ERROR:
                    job.status = JobStatus.FAILED
                    self._failed.append(job)
                else:
                    job.status = JobStatus.COMPLETED
                    self._completed.append(job)
                    self._processing_times.append(job.completed_at - job.started_at)

            except Exception as e:
                job.status = JobStatus.FAILED
                job.result = TaskResult(
                    task_id=job.id,
                    status=TaskStatus.ERROR,
                    result={"error": str(e)},
                )
                job.completed_at = time.time()
                self._failed.append(job)

            finally:
                self._running.pop(job.id, None)
                for callback in self._callbacks:
                    try:
                        callback(job)
                    except Exception:
                        pass

    async def process_all(self, progress_callback: Callable[[QueueStats], None] | None = None):
        """Process all jobs in the queue."""
        self._running_flag = True

        # Sort by priority (higher first)
        sorted_queue = sorted(self._queue, key=lambda j: -j.priority)
        self._queue = deque(sorted_queue)

        tasks = []
        while self._queue or self._running:
            # Start new tasks up to concurrency limit
            while self._queue and len(self._running) < self.max_concurrent:
                job = self._queue.popleft()
                task = asyncio.create_task(self._process_job(job))
                tasks.append(task)

            # Report progress
            if progress_callback:
                progress_callback(self.stats)

            # Wait a bit before checking again
            await asyncio.sleep(0.1)

        # Wait for all tasks to complete
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._running_flag = False

    async def process_stream(self):
        """Process jobs as a stream, yielding results."""
        while self._queue:
            job = self._queue.popleft()
            await self._process_job(job)
            yield job

    @property
    def stats(self) -> QueueStats:
        """Get current queue statistics."""
        total = len(self._queue) + len(self._running) + len(self._completed) + len(self._failed)
        avg_time = sum(self._processing_times) / len(self._processing_times) if self._processing_times else 0
        throughput = 60 / avg_time if avg_time > 0 else 0

        return QueueStats(
            total_jobs=total,
            pending=len(self._queue),
            running=len(self._running),
            completed=len(self._completed),
            failed=len(self._failed),
            avg_processing_time=avg_time,
            throughput_per_minute=throughput,
        )

    def get_results(self) -> list[TaskResult]:
        """Get all completed results."""
        return [job.result for job in self._completed if job.result]

    def get_failed(self) -> list[Job]:
        """Get all failed jobs."""
        return self._failed.copy()

    def export_results(self, output_path: Path):
        """Export results to JSON file."""
        results = []
        for job in self._completed:
            if job.result:
                results.append({
                    "job_id": job.id,
                    "task_type": job.task_type,
                    "input": job.input_data,
                    "result": job.result.result,
                    "confidence": job.result.confidence,
                    "status": job.result.status.value,
                    "processing_time": (job.completed_at or 0) - (job.started_at or 0),
                })

        with open(output_path, "w") as f:
            json.dump(results, f, indent=2)

    def clear(self):
        """Clear the queue and reset state."""
        self._queue.clear()
        self._running.clear()
        self._completed.clear()
        self._failed.clear()
        self._processing_times.clear()


class BatchProcessor:
    """High-level batch processor for VEX corpus generation."""

    def __init__(
        self,
        client: OllamaClient | None = None,
        tier1_concurrency: int = 10,
        tier2_concurrency: int = 3,
    ):
        self.client = client or OllamaClient()
        self.tier1_queue = JobQueue(self.client, max_concurrent=tier1_concurrency)
        self.tier2_queue = JobQueue(self.client, max_concurrent=tier2_concurrency)

    def add_sample(self, code: str, sample_id: str | None = None):
        """Add a VEX sample for full processing pipeline."""
        base_id = sample_id or f"sample_{time.time():.0f}"

        # Tier 1 tasks
        self.tier1_queue.add_job("classify_wrangle", {"code": code}, f"{base_id}_classify")
        self.tier1_queue.add_job("extract_attributes", {"code": code}, f"{base_id}_attrs")
        self.tier1_queue.add_job("extract_functions", {"code": code}, f"{base_id}_funcs")
        self.tier1_queue.add_job("detect_bugs", {"code": code}, f"{base_id}_bugs")

        # Tier 2 tasks (lower priority, run after tier 1)
        self.tier2_queue.add_job(
            "generate_explanation",
            {"code": code, "audience": "intermediate"},
            f"{base_id}_explain",
            priority=-1,
        )
        self.tier2_queue.add_job(
            "generate_prompt",
            {"code": code},
            f"{base_id}_prompt",
            priority=-1,
        )

    async def process_all(self, progress_callback=None):
        """Process all samples through both tiers."""
        # Process Tier 1 first (fast)
        await self.tier1_queue.process_all(progress_callback)

        # Then Tier 2 (slower)
        await self.tier2_queue.process_all(progress_callback)

    def get_all_results(self) -> dict[str, list[TaskResult]]:
        """Get results from both tiers."""
        return {
            "tier1": self.tier1_queue.get_results(),
            "tier2": self.tier2_queue.get_results(),
        }

    @property
    def stats(self) -> dict[str, QueueStats]:
        """Get stats for both tiers."""
        return {
            "tier1": self.tier1_queue.stats,
            "tier2": self.tier2_queue.stats,
        }
