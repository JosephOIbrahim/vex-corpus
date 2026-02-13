#!/usr/bin/env python3
"""
VEX Corpus Orchestrator - Async Subagent Architecture

A sophisticated orchestration layer that coordinates multiple async subagents
for parallel, fault-tolerant corpus processing with intelligent work distribution.

Architecture:
    ┌─────────────────────────────────────────────────────────────────┐
    │                      ORCHESTRATOR                                │
    │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
    │  │   Ingestion  │  │  Processing  │  │    Export    │          │
    │  │   Subagent   │  │   Subagent   │  │   Subagent   │          │
    │  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘          │
    │         │                 │                 │                   │
    │         ▼                 ▼                 ▼                   │
    │  ┌─────────────────────────────────────────────────────────┐   │
    │  │                    WORK QUEUES                          │   │
    │  │  [ingest_q] ──▶ [tier1_q] ──▶ [tier2_q] ──▶ [export_q] │   │
    │  └─────────────────────────────────────────────────────────┘   │
    │                           │                                     │
    │                           ▼                                     │
    │  ┌─────────────────────────────────────────────────────────┐   │
    │  │                   STATE STORE                           │   │
    │  │  samples: Dict[id, VEXSample]                          │   │
    │  │  metrics: ProcessingMetrics                            │   │
    │  └─────────────────────────────────────────────────────────┘   │
    └─────────────────────────────────────────────────────────────────┘

Usage:
    from orchestrator import Orchestrator

    async with Orchestrator() as orch:
        await orch.add_source("./vex_files")
        await orch.run_until_complete()
        await orch.export_all()
"""

import asyncio
import json
import signal
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar, Generic
from contextlib import asynccontextmanager
import traceback

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).parent))

from ollama.src.client import OllamaClient
from ollama.src.dispatcher import TaskDispatcher
from ollama.src.models import TaskStatus
from pipeline.intake import IntakePipeline, VEXSample, ProcessingStage, VEXParser


# ============================================================================
# Core Types
# ============================================================================

class SubagentState(Enum):
    IDLE = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPING = auto()
    STOPPED = auto()
    ERROR = auto()


class ExportFormat(Enum):
    JSON_CORPUS = "json_corpus"
    JSONL_TRAINING = "jsonl_training"
    JSONL_CHAT = "jsonl_chat"
    CSV_METADATA = "csv_metadata"
    PARQUET = "parquet"
    HUGGINGFACE = "huggingface"


@dataclass
class SubagentMetrics:
    """Metrics for a single subagent."""
    name: str
    state: SubagentState = SubagentState.IDLE
    items_processed: int = 0
    items_failed: int = 0
    total_time_ms: float = 0
    last_activity: datetime = field(default_factory=datetime.now)
    errors: list = field(default_factory=list)

    @property
    def avg_time_ms(self) -> float:
        if self.items_processed == 0:
            return 0
        return self.total_time_ms / self.items_processed

    @property
    def success_rate(self) -> float:
        total = self.items_processed + self.items_failed
        if total == 0:
            return 1.0
        return self.items_processed / total


@dataclass
class OrchestratorMetrics:
    """Global orchestrator metrics."""
    started_at: datetime = field(default_factory=datetime.now)
    total_ingested: int = 0
    total_processed: int = 0
    total_exported: int = 0
    subagent_metrics: dict = field(default_factory=dict)

    @property
    def uptime(self) -> timedelta:
        return datetime.now() - self.started_at

    @property
    def throughput_per_hour(self) -> float:
        hours = self.uptime.total_seconds() / 3600
        if hours < 0.001:
            return 0
        return self.total_processed / hours


T = TypeVar('T')


class AsyncQueue(Generic[T]):
    """Enhanced async queue with metrics and peek."""

    def __init__(self, name: str, maxsize: int = 0):
        self.name = name
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.total_put: int = 0
        self.total_get: int = 0

    async def put(self, item: T):
        await self._queue.put(item)
        self.total_put += 1

    async def get(self, timeout: float = None) -> Optional[T]:
        try:
            if timeout:
                return await asyncio.wait_for(self._queue.get(), timeout)
            return await self._queue.get()
        except asyncio.TimeoutError:
            return None

    def qsize(self) -> int:
        return self._queue.qsize()

    def empty(self) -> bool:
        return self._queue.empty()

    @property
    def pending(self) -> int:
        return self.total_put - self.total_get


# ============================================================================
# Subagent Base Class
# ============================================================================

class Subagent(ABC):
    """Base class for all subagents."""

    def __init__(self, name: str, orchestrator: 'Orchestrator'):
        self.name = name
        self.orchestrator = orchestrator
        self.metrics = SubagentMetrics(name=name)
        self._task: Optional[asyncio.Task] = None
        self._running = False

    @property
    def state(self) -> SubagentState:
        return self.metrics.state

    def log(self, msg: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[{timestamp}] [{self.name}] {msg}")

    async def start(self):
        """Start the subagent."""
        if self._running:
            return

        self._running = True
        self.metrics.state = SubagentState.RUNNING
        self._task = asyncio.create_task(self._run_loop())
        self.log("Started")

    async def stop(self):
        """Stop the subagent gracefully."""
        self.metrics.state = SubagentState.STOPPING
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        self.metrics.state = SubagentState.STOPPED
        self.log("Stopped")

    async def _run_loop(self):
        """Main run loop - override in subclasses."""
        try:
            await self.run()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.metrics.state = SubagentState.ERROR
            self.metrics.errors.append((str(e), datetime.now()))
            self.log(f"Error: {e}", "ERROR")
            traceback.print_exc()

    @abstractmethod
    async def run(self):
        """Subagent main logic - must be implemented."""
        pass

    def record_success(self, duration_ms: float):
        """Record a successful operation."""
        self.metrics.items_processed += 1
        self.metrics.total_time_ms += duration_ms
        self.metrics.last_activity = datetime.now()

    def record_failure(self, error: str):
        """Record a failed operation."""
        self.metrics.items_failed += 1
        self.metrics.errors.append((error, datetime.now()))
        if len(self.metrics.errors) > 50:
            self.metrics.errors = self.metrics.errors[-50:]


# ============================================================================
# Specialized Subagents
# ============================================================================

class IngestionSubagent(Subagent):
    """Watches sources and ingests VEX files into the pipeline."""

    def __init__(self, orchestrator: 'Orchestrator'):
        super().__init__("Ingest", orchestrator)
        self.sources: list[Path] = []
        self.seen_files: set[Path] = set()
        self.scan_interval: float = 5.0

    def add_source(self, path: Path):
        if path not in self.sources:
            self.sources.append(path)
            self.log(f"Added source: {path}")

    async def run(self):
        while self._running:
            for source in self.sources:
                if not source.exists():
                    continue

                await self._scan_source(source)

            await asyncio.sleep(self.scan_interval)

    async def _scan_source(self, source: Path):
        """Scan a source for new files."""
        extensions = {'.vex', '.vfl', '.h', '.md', '.txt', '.json'}

        if source.is_file():
            files = [source] if source.suffix in extensions else []
        else:
            files = [f for f in source.rglob("*") if f.suffix in extensions]

        for file_path in files:
            if file_path in self.seen_files:
                continue

            self.seen_files.add(file_path)

            start = time.time()
            try:
                samples = VEXParser.extract_from_file(file_path)

                for sample in samples:
                    # Check dedup
                    if sample.hash not in self.orchestrator.seen_hashes:
                        self.orchestrator.seen_hashes.add(sample.hash)
                        self.orchestrator.samples[sample.id] = sample
                        await self.orchestrator.tier1_queue.put(sample)
                        self.orchestrator.metrics.total_ingested += 1

                duration = (time.time() - start) * 1000
                self.record_success(duration)

                if samples:
                    self.log(f"{file_path.name}: {len(samples)} samples")

            except Exception as e:
                self.record_failure(str(e))


class Tier1Subagent(Subagent):
    """Processes samples through Tier 1 classification."""

    def __init__(self, orchestrator: 'Orchestrator', worker_id: int = 0):
        super().__init__(f"Tier1-{worker_id}", orchestrator)
        self.worker_id = worker_id
        self.dispatcher: Optional[TaskDispatcher] = None

        self.tasks = [
            "classify_wrangle",
            "extract_attributes",
            "extract_functions",
            "detect_bugs",
            "estimate_complexity",
            "classify_topic",
        ]

    async def run(self):
        # Initialize dispatcher
        self.dispatcher = TaskDispatcher(client=self.orchestrator.client)

        while self._running:
            sample = await self.orchestrator.tier1_queue.get(timeout=1.0)
            if sample is None:
                continue

            start = time.time()
            try:
                await self._process_sample(sample)
                duration = (time.time() - start) * 1000
                self.record_success(duration)

                # Route to next stage
                if sample.flagged_for_review or not self.orchestrator.enable_tier2:
                    sample.stage = ProcessingStage.COMPLETE
                    await self.orchestrator.export_queue.put(sample)
                    self.orchestrator.metrics.total_processed += 1
                else:
                    await self.orchestrator.tier2_queue.put(sample)

            except Exception as e:
                self.record_failure(str(e))
                sample.flagged_for_review = True
                sample.review_reason = f"Tier1 error: {str(e)[:50]}"

    async def _process_sample(self, sample: VEXSample):
        """Run all tier1 tasks on a sample."""
        sample.stage = ProcessingStage.TIER1_CLASSIFICATION

        # Run tasks with limited concurrency
        sem = asyncio.Semaphore(2)

        async def run_task(task_type: str):
            async with sem:
                return await self.dispatcher.dispatch(
                    task_type,
                    {"code": sample.code},
                    task_id=f"{sample.id}_{task_type}"
                )

        results = await asyncio.gather(
            *[run_task(t) for t in self.tasks],
            return_exceptions=True
        )

        # Apply results
        for task_type, result in zip(self.tasks, results):
            if isinstance(result, Exception):
                continue
            if result.status != TaskStatus.SUCCESS:
                continue

            r = result.result

            if task_type == "classify_wrangle":
                sample.context = r.get("context", "unknown")
                sample.context_confidence = result.confidence
            elif task_type == "extract_attributes":
                sample.attributes_read = r.get("reads", [])
                sample.attributes_written = r.get("writes", [])
                sample.channels = r.get("channels", [])
            elif task_type == "extract_functions":
                sample.functions = r.get("functions", [])
            elif task_type == "detect_bugs":
                sample.bugs = r.get("bugs", [])
            elif task_type == "estimate_complexity":
                sample.complexity = r.get("complexity", "")
            elif task_type == "classify_topic":
                sample.topic = r.get("primary_topic", "")

        # Check for flags
        if sample.context_confidence < 0.6:
            sample.flagged_for_review = True
            sample.review_reason = f"Low confidence: {sample.context_confidence:.2f}"


class Tier2Subagent(Subagent):
    """Processes samples through Tier 2 generation."""

    def __init__(self, orchestrator: 'Orchestrator', worker_id: int = 0):
        super().__init__(f"Tier2-{worker_id}", orchestrator)
        self.worker_id = worker_id
        self.dispatcher: Optional[TaskDispatcher] = None

        self.tasks = [
            "generate_prompt",
            "generate_explanation",
            "rate_difficulty",
        ]

    async def run(self):
        self.dispatcher = TaskDispatcher(client=self.orchestrator.client)

        while self._running:
            sample = await self.orchestrator.tier2_queue.get(timeout=1.0)
            if sample is None:
                continue

            start = time.time()
            try:
                await self._process_sample(sample)
                duration = (time.time() - start) * 1000
                self.record_success(duration)

                sample.stage = ProcessingStage.COMPLETE
                await self.orchestrator.export_queue.put(sample)
                self.orchestrator.metrics.total_processed += 1

            except Exception as e:
                self.record_failure(str(e))
                sample.stage = ProcessingStage.COMPLETE  # Still export with partial data

    async def _process_sample(self, sample: VEXSample):
        """Run tier2 tasks sequentially (heavier model)."""
        sample.stage = ProcessingStage.TIER2_GENERATION

        for task_type in self.tasks:
            try:
                input_data = {"code": sample.code}
                if task_type == "generate_explanation":
                    input_data["audience"] = "intermediate"
                    input_data["task"] = sample.topic or "VEX operation"

                result = await self.dispatcher.dispatch(
                    task_type,
                    input_data,
                    task_id=f"{sample.id}_{task_type}"
                )

                if result.status != TaskStatus.SUCCESS:
                    continue

                r = result.result

                if task_type == "generate_prompt":
                    sample.prompt = r.get("prompt", "")
                    sample.alternative_prompts = r.get("alternative_phrasings", [])
                elif task_type == "generate_explanation":
                    sample.explanation = r.get("explanation", "")
                elif task_type == "rate_difficulty":
                    sample.difficulty = r.get("difficulty", 0)

            except Exception as e:
                self.log(f"Task {task_type} failed: {e}", "WARN")


class ExportSubagent(Subagent):
    """Handles export to multiple formats."""

    def __init__(self, orchestrator: 'Orchestrator'):
        super().__init__("Export", orchestrator)
        self.output_dir = Path("C:/Users/User/vex-corpus/output")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.batch: list[VEXSample] = []
        self.batch_size: int = 50
        self.flush_interval: float = 60.0
        self.last_flush: float = time.time()

        self.formats: set[ExportFormat] = {
            ExportFormat.JSON_CORPUS,
            ExportFormat.JSONL_TRAINING,
        }

    def add_format(self, fmt: ExportFormat):
        self.formats.add(fmt)

    async def run(self):
        while self._running:
            sample = await self.orchestrator.export_queue.get(timeout=1.0)

            if sample:
                self.batch.append(sample)

            # Flush if batch full or timeout
            should_flush = (
                len(self.batch) >= self.batch_size or
                (self.batch and time.time() - self.last_flush > self.flush_interval)
            )

            if should_flush:
                await self._flush_batch()

    async def _flush_batch(self):
        """Flush current batch to all enabled formats."""
        if not self.batch:
            return

        start = time.time()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        try:
            for fmt in self.formats:
                await self._export_format(fmt, timestamp)

            self.orchestrator.metrics.total_exported += len(self.batch)
            duration = (time.time() - start) * 1000
            self.record_success(duration)

            self.log(f"Exported {len(self.batch)} samples")

        except Exception as e:
            self.record_failure(str(e))

        self.batch = []
        self.last_flush = time.time()

    async def _export_format(self, fmt: ExportFormat, timestamp: str):
        """Export to a specific format."""
        if fmt == ExportFormat.JSON_CORPUS:
            path = self.output_dir / f"corpus_{timestamp}.json"
            corpus = {
                "metadata": {
                    "generated_at": datetime.now().isoformat(),
                    "sample_count": len(self.batch),
                },
                "samples": [s.to_dict() for s in self.batch]
            }
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(corpus, f, indent=2, ensure_ascii=False)

        elif fmt == ExportFormat.JSONL_TRAINING:
            path = self.output_dir / f"training_{timestamp}.jsonl"
            with open(path, 'w', encoding='utf-8') as f:
                for sample in self.batch:
                    pair = sample.to_training_pair()
                    if pair:
                        f.write(json.dumps(pair, ensure_ascii=False) + '\n')

        elif fmt == ExportFormat.JSONL_CHAT:
            # OpenAI chat format
            path = self.output_dir / f"chat_{timestamp}.jsonl"
            with open(path, 'w', encoding='utf-8') as f:
                for sample in self.batch:
                    if not sample.prompt:
                        continue
                    chat = {
                        "messages": [
                            {"role": "user", "content": sample.prompt},
                            {"role": "assistant", "content": sample.code}
                        ]
                    }
                    f.write(json.dumps(chat, ensure_ascii=False) + '\n')

        elif fmt == ExportFormat.CSV_METADATA:
            import csv
            path = self.output_dir / f"metadata_{timestamp}.csv"
            with open(path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['id', 'context', 'topic', 'complexity', 'difficulty', 'flagged'])
                for s in self.batch:
                    writer.writerow([s.id, s.context, s.topic, s.complexity, s.difficulty, s.flagged_for_review])

    async def force_flush(self):
        """Force immediate flush of all pending samples."""
        await self._flush_batch()

    async def export_all(self, formats: list[ExportFormat] = None):
        """Export all samples in the orchestrator to specified formats."""
        formats = formats or list(self.formats)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        all_samples = list(self.orchestrator.samples.values())
        complete = [s for s in all_samples if s.stage == ProcessingStage.COMPLETE]

        self.log(f"Exporting {len(complete)} complete samples...")

        # Temporarily set batch to all complete samples
        old_batch = self.batch
        self.batch = complete

        for fmt in formats:
            try:
                await self._export_format(fmt, timestamp)
                self.log(f"Exported: {fmt.value}")
            except Exception as e:
                self.log(f"Export failed for {fmt.value}: {e}", "ERROR")

        self.batch = old_batch


# ============================================================================
# Main Orchestrator
# ============================================================================

class Orchestrator:
    """
    Central orchestrator coordinating all subagents.

    Usage:
        async with Orchestrator() as orch:
            await orch.add_source(Path("./vex_files"))
            await orch.run_until_complete()
            await orch.export_all()
    """

    def __init__(
        self,
        tier1_workers: int = 2,
        tier2_workers: int = 1,
        enable_tier2: bool = True,
        enable_houdini: bool = False,
        houdini_port: int = 9008,
    ):
        # Ollama client
        self.client: Optional[OllamaClient] = None

        # State
        self.samples: dict[str, VEXSample] = {}
        self.seen_hashes: set[str] = set()
        self.metrics = OrchestratorMetrics()

        # Queues
        self.tier1_queue: AsyncQueue[VEXSample] = AsyncQueue("tier1")
        self.tier2_queue: AsyncQueue[VEXSample] = AsyncQueue("tier2")
        self.export_queue: AsyncQueue[VEXSample] = AsyncQueue("export")

        # Configuration
        self.tier1_workers = tier1_workers
        self.tier2_workers = tier2_workers
        self.enable_tier2 = enable_tier2
        self.enable_houdini = enable_houdini
        self.houdini_port = houdini_port

        # Subagents
        self.ingest_agent: Optional[IngestionSubagent] = None
        self.tier1_agents: list[Tier1Subagent] = []
        self.tier2_agents: list[Tier2Subagent] = []
        self.export_agent: Optional[ExportSubagent] = None
        self.houdini_agent = None  # Optional HoudiniSubagent

        self._running = False

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.shutdown()

    async def initialize(self):
        """Initialize the orchestrator and all subagents."""
        print("[ORCH] Initializing orchestrator...")

        # Initialize Ollama client
        self.client = OllamaClient()
        health = await self.client.health_check()

        if health['status'] != 'healthy':
            raise RuntimeError(f"Ollama not healthy: {health}")

        print(f"[ORCH] Ollama connected: {len(health['available_models'])} models")

        # Pre-warm primary model
        print("[ORCH] Pre-warming nemotron-mini...")
        await self._warm_model("nemotron-mini:latest")

        # Create subagents
        self.ingest_agent = IngestionSubagent(self)
        self.export_agent = ExportSubagent(self)

        for i in range(self.tier1_workers):
            self.tier1_agents.append(Tier1Subagent(self, i))

        if self.enable_tier2:
            for i in range(self.tier2_workers):
                self.tier2_agents.append(Tier2Subagent(self, i))

        # Optional Houdini integration
        if self.enable_houdini:
            try:
                from houdini.bridge import HoudiniSubagent
                self.houdini_agent = HoudiniSubagent(self, port=self.houdini_port)
                print(f"[ORCH] Houdini integration enabled on port {self.houdini_port}")
            except ImportError as e:
                print(f"[ORCH] Warning: Houdini bridge not available: {e}")
            except Exception as e:
                print(f"[ORCH] Warning: Could not initialize Houdini agent: {e}")

        print(f"[ORCH] Created {len(self.tier1_agents)} Tier1 + {len(self.tier2_agents)} Tier2 workers")
        print("[ORCH] Initialization complete")

    async def _warm_model(self, model: str):
        """Pre-load model into VRAM."""
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    "http://localhost:11434/api/generate",
                    json={"model": model, "prompt": "test", "options": {"num_predict": 1}},
                    timeout=60.0
                )
        except Exception as e:
            print(f"[ORCH] Warning: Could not pre-warm {model}: {e}")

    async def add_source(self, path: Path):
        """Add a source directory or file to watch."""
        if isinstance(path, str):
            path = Path(path)
        self.ingest_agent.add_source(path)

    def add_export_format(self, fmt: ExportFormat):
        """Add an export format."""
        self.export_agent.add_format(fmt)

    async def start(self):
        """Start all subagents."""
        self._running = True

        await self.ingest_agent.start()
        await self.export_agent.start()

        for agent in self.tier1_agents:
            await agent.start()

        for agent in self.tier2_agents:
            await agent.start()

        # Start Houdini agent if enabled
        if self.houdini_agent:
            await self.houdini_agent.start()

        print("[ORCH] All subagents started")

    async def shutdown(self):
        """Shutdown all subagents gracefully."""
        print("[ORCH] Shutting down...")
        self._running = False

        # Stop Houdini agent first
        if self.houdini_agent:
            await self.houdini_agent.stop()

        # Stop in reverse order
        for agent in self.tier2_agents:
            await agent.stop()

        for agent in self.tier1_agents:
            await agent.stop()

        await self.export_agent.force_flush()
        await self.export_agent.stop()
        await self.ingest_agent.stop()

        print("[ORCH] Shutdown complete")

    async def run_until_complete(self, timeout: float = None):
        """Run until all queues are empty and processing is complete."""
        await self.start()

        start_time = time.time()
        stable_count = 0
        last_state = (0, 0)  # (ingested, processed)

        # Wait a moment for ingestion to begin
        await asyncio.sleep(2.0)

        while self._running:
            await asyncio.sleep(1.0)

            # Check progress
            current_state = (self.metrics.total_ingested, self.metrics.total_processed)
            if current_state == last_state:
                stable_count += 1
            else:
                stable_count = 0
                last_state = current_state

            # Print status periodically
            if int(time.time()) % 10 == 0:
                self._print_status()

            # Check completion conditions
            all_empty = (
                self.tier1_queue.empty() and
                self.tier2_queue.empty() and
                self.export_queue.empty()
            )

            # Count samples not yet complete
            pending = sum(1 for s in self.samples.values() if s.stage != ProcessingStage.COMPLETE)

            # Stable for 5 seconds with empty queues and no pending means done
            if all_empty and pending == 0 and stable_count >= 5 and self.metrics.total_ingested > 0:
                print("[ORCH] Processing complete")
                break

            # Timeout check
            if timeout and (time.time() - start_time) > timeout:
                print("[ORCH] Timeout reached")
                break

    async def run_forever(self):
        """Run continuously until interrupted."""
        await self.start()

        # Setup signal handling
        shutdown_event = asyncio.Event()

        def signal_handler():
            print("\n[ORCH] Interrupt received")
            shutdown_event.set()

        if sys.platform != 'win32':
            loop = asyncio.get_event_loop()
            loop.add_signal_handler(signal.SIGINT, signal_handler)
            loop.add_signal_handler(signal.SIGTERM, signal_handler)

        # Status printer
        async def status_loop():
            while self._running:
                await asyncio.sleep(30)
                self._print_status()

        status_task = asyncio.create_task(status_loop())

        try:
            await shutdown_event.wait()
        except KeyboardInterrupt:
            pass
        finally:
            status_task.cancel()

    async def export_all(self, formats: list[ExportFormat] = None):
        """Export all complete samples."""
        await self.export_agent.export_all(formats)

    def _print_status(self):
        """Print current status."""
        m = self.metrics
        print("\n" + "=" * 60)
        print(f"ORCHESTRATOR STATUS - {datetime.now().strftime('%H:%M:%S')}")
        print("=" * 60)
        print(f"Uptime: {str(m.uptime).split('.')[0]}")
        print(f"Throughput: {m.throughput_per_hour:.1f} samples/hour")
        print("-" * 60)
        print(f"Ingested:  {m.total_ingested:>6}")
        print(f"Processed: {m.total_processed:>6}")
        print(f"Exported:  {m.total_exported:>6}")
        print("-" * 60)
        print(f"Tier1 Queue: {self.tier1_queue.qsize():>4}")
        print(f"Tier2 Queue: {self.tier2_queue.qsize():>4}")
        print(f"Export Queue: {self.export_queue.qsize():>4}")
        print("-" * 60)

        # Subagent status
        agents = [self.ingest_agent] + self.tier1_agents + self.tier2_agents + [self.export_agent]
        for agent in agents:
            if agent:
                status = f"{agent.metrics.items_processed} done"
                if agent.metrics.items_failed:
                    status += f", {agent.metrics.items_failed} failed"
                print(f"  {agent.name}: {status}")

        print("=" * 60 + "\n")

    def get_stats(self) -> dict:
        """Get comprehensive statistics."""
        samples = list(self.samples.values())

        by_stage = {}
        for stage in ProcessingStage:
            by_stage[stage.value] = sum(1 for s in samples if s.stage == stage)

        by_context = {}
        for s in samples:
            ctx = s.context or "unknown"
            by_context[ctx] = by_context.get(ctx, 0) + 1

        return {
            "total_samples": len(samples),
            "by_stage": by_stage,
            "by_context": by_context,
            "with_prompts": sum(1 for s in samples if s.prompt),
            "flagged": sum(1 for s in samples if s.flagged_for_review),
            "metrics": {
                "uptime_seconds": self.metrics.uptime.total_seconds(),
                "throughput_per_hour": self.metrics.throughput_per_hour,
                "total_ingested": self.metrics.total_ingested,
                "total_processed": self.metrics.total_processed,
                "total_exported": self.metrics.total_exported,
            }
        }


# ============================================================================
# CLI Entry Point
# ============================================================================

async def main():
    import argparse

    parser = argparse.ArgumentParser(description="VEX Corpus Orchestrator")
    parser.add_argument('sources', nargs='*', type=Path, help='Source directories/files')
    parser.add_argument('--once', action='store_true', help='Process once and exit')
    parser.add_argument('--no-tier2', action='store_true', help='Skip Tier 2 generation')
    parser.add_argument('--tier1-workers', type=int, default=2, help='Tier 1 worker count')
    parser.add_argument('--tier2-workers', type=int, default=1, help='Tier 2 worker count')
    parser.add_argument('--export', nargs='*', choices=['json', 'jsonl', 'chat', 'csv'],
                        help='Export formats')
    parser.add_argument('--timeout', type=int, help='Processing timeout in seconds')

    args = parser.parse_args()

    # Default sources
    sources = args.sources or [
        Path("C:/Users/User/vex-corpus/input"),
        Path("C:/Users/User/vex-corpus/watch"),
    ]

    # Create directories
    for s in sources:
        if not s.suffix:  # Is directory
            s.mkdir(parents=True, exist_ok=True)

    print("\n" + "#" * 60)
    print("#  VEX CORPUS ORCHESTRATOR")
    print("#" * 60 + "\n")

    async with Orchestrator(
        tier1_workers=args.tier1_workers,
        tier2_workers=args.tier2_workers,
        enable_tier2=not args.no_tier2,
    ) as orch:
        # Add sources
        for source in sources:
            await orch.add_source(source)

        # Add export formats
        if args.export:
            format_map = {
                'json': ExportFormat.JSON_CORPUS,
                'jsonl': ExportFormat.JSONL_TRAINING,
                'chat': ExportFormat.JSONL_CHAT,
                'csv': ExportFormat.CSV_METADATA,
            }
            for fmt in args.export:
                orch.add_export_format(format_map[fmt])

        # Run
        if args.once:
            await orch.run_until_complete(timeout=args.timeout)
        else:
            await orch.run_forever()

        # Final export
        await orch.export_all()

        # Print final stats
        stats = orch.get_stats()
        print("\n" + "#" * 60)
        print("#  FINAL STATISTICS")
        print("#" * 60)
        print(f"Total Samples: {stats['total_samples']}")
        print(f"Complete: {stats['by_stage'].get('complete', 0)}")
        print(f"With Prompts: {stats['with_prompts']}")
        print(f"Flagged: {stats['flagged']}")
        print("#" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
