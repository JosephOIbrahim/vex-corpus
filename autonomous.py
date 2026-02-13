#!/usr/bin/env python3
"""
VEX Corpus Autonomous Processing Daemon

A fully autonomous, self-healing pipeline that:
1. Watches directories for new VEX files
2. Automatically ingests and processes through both tiers
3. Exports training data continuously
4. Monitors Ollama health and recovers from failures
5. Manages model loading/unloading for optimal VRAM usage
6. Provides real-time statistics and progress

Usage:
    python autonomous.py                    # Interactive mode with live dashboard
    python autonomous.py --daemon           # Background daemon mode
    python autonomous.py --once ./folder    # Process folder once and exit
"""

import asyncio
import argparse
import json
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from enum import Enum

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).parent))

from ollama.src.client import OllamaClient
from ollama.src.models import TaskStatus
from pipeline.intake import IntakePipeline, VEXSample, ProcessingStage, VEXParser


class DaemonState(str, Enum):
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    RECOVERING = "recovering"
    STOPPED = "stopped"


@dataclass
class ProcessingStats:
    """Runtime statistics."""
    started_at: datetime = field(default_factory=datetime.now)
    samples_ingested: int = 0
    samples_processed: int = 0
    samples_failed: int = 0
    tier1_tasks_completed: int = 0
    tier2_tasks_completed: int = 0
    exports_completed: int = 0
    errors: list = field(default_factory=list)
    last_activity: datetime = field(default_factory=datetime.now)

    @property
    def uptime(self) -> timedelta:
        return datetime.now() - self.started_at

    @property
    def throughput_per_hour(self) -> float:
        hours = self.uptime.total_seconds() / 3600
        if hours < 0.01:
            return 0.0
        return self.samples_processed / hours


@dataclass
class WatchTarget:
    """A directory or file to watch."""
    path: Path
    recursive: bool = True
    last_scan: datetime = field(default_factory=lambda: datetime.min)
    files_seen: set = field(default_factory=set)


class AutonomousDaemon:
    """Self-managing VEX corpus processing daemon."""

    EXTENSIONS = {'.vex', '.vfl', '.h', '.md', '.txt', '.json'}

    def __init__(
        self,
        watch_dirs: list[Path] = None,
        output_dir: Path = None,
        scan_interval: float = 10.0,
        export_interval: float = 300.0,  # Export every 5 minutes
        health_check_interval: float = 30.0,
        auto_export: bool = True,
        tier2_enabled: bool = True,
    ):
        self.watch_dirs = [WatchTarget(path=p) for p in (watch_dirs or [])]
        self.output_dir = output_dir or Path("C:/Users/User/vex-corpus/output")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.scan_interval = scan_interval
        self.export_interval = export_interval
        self.health_check_interval = health_check_interval
        self.auto_export = auto_export
        self.tier2_enabled = tier2_enabled

        # Core components
        self.client: Optional[OllamaClient] = None
        self.pipeline: Optional[IntakePipeline] = None

        # State
        self.state = DaemonState.STARTING
        self.stats = ProcessingStats()
        self._running = False
        self._shutdown_event = asyncio.Event()

        # Queues
        self._pending_files: asyncio.Queue = asyncio.Queue()
        self._pending_samples: asyncio.Queue = asyncio.Queue()

        # Model state tracking
        self._current_model: Optional[str] = None
        self._model_load_time: Optional[datetime] = None

    async def initialize(self) -> bool:
        """Initialize the daemon and verify Ollama connectivity."""
        print("[INIT] Starting VEX Corpus Autonomous Daemon...")

        try:
            self.client = OllamaClient()
            health = await self.client.health_check()

            if health['status'] != 'healthy':
                print(f"[ERROR] Ollama not healthy: {health}")
                return False

            print(f"[INIT] Ollama connected: {len(health['available_models'])} models available")

            # Verify Nemotron models
            nemotron_models = [m for m in health['available_models'] if 'nemotron' in m.lower()]
            if not nemotron_models:
                print("[ERROR] No Nemotron models found! Please pull nemotron-mini and nemotron-3-nano")
                return False

            print(f"[INIT] Nemotron models: {', '.join(nemotron_models)}")

            # Initialize pipeline
            self.pipeline = IntakePipeline(output_dir=self.output_dir)

            # Register callbacks
            self.pipeline.on_progress(self._on_progress)
            self.pipeline.on_sample_complete(self._on_sample_complete)
            self.pipeline.on_error(self._on_error)

            # Pre-warm the primary model
            print("[INIT] Pre-warming nemotron-mini...")
            await self._warm_model("nemotron-mini:latest")

            self.state = DaemonState.HEALTHY
            print("[INIT] Initialization complete")
            return True

        except Exception as e:
            print(f"[ERROR] Initialization failed: {e}")
            self.stats.errors.append(("init", str(e), datetime.now()))
            return False

    async def _warm_model(self, model: str):
        """Pre-load a model into VRAM."""
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                # Send a minimal request to load the model
                await client.post(
                    "http://localhost:11434/api/generate",
                    json={
                        "model": model,
                        "prompt": "test",
                        "options": {"num_predict": 1}
                    },
                    timeout=60.0
                )
            self._current_model = model
            self._model_load_time = datetime.now()
            print(f"[MODEL] {model} loaded into VRAM")
        except Exception as e:
            print(f"[WARN] Failed to pre-warm {model}: {e}")

    def _on_progress(self, stage: str, current: int, total: int):
        """Progress callback."""
        self.stats.last_activity = datetime.now()
        if "Tier 1" in stage:
            self.stats.tier1_tasks_completed = current
        elif "Tier 2" in stage:
            self.stats.tier2_tasks_completed = current

    def _on_sample_complete(self, sample: VEXSample):
        """Sample completion callback."""
        self.stats.samples_processed += 1
        self.stats.last_activity = datetime.now()

    def _on_error(self, source: str, error: Exception):
        """Error callback."""
        self.stats.errors.append((source, str(error), datetime.now()))
        self.stats.samples_failed += 1
        # Keep only last 100 errors
        if len(self.stats.errors) > 100:
            self.stats.errors = self.stats.errors[-100:]

    async def add_watch_directory(self, path: Path, recursive: bool = True):
        """Add a directory to watch."""
        if not path.exists():
            print(f"[WARN] Watch directory does not exist: {path}")
            return

        target = WatchTarget(path=path, recursive=recursive)
        self.watch_dirs.append(target)
        print(f"[WATCH] Added: {path} (recursive={recursive})")

        # Immediately scan
        await self._scan_directory(target)

    async def _scan_directory(self, target: WatchTarget):
        """Scan a directory for new files."""
        if not target.path.exists():
            return

        pattern = "**/*" if target.recursive else "*"

        for ext in self.EXTENSIONS:
            for file_path in target.path.glob(f"{pattern}{ext}"):
                if file_path not in target.files_seen:
                    target.files_seen.add(file_path)
                    await self._pending_files.put(file_path)
                    self.stats.last_activity = datetime.now()

        target.last_scan = datetime.now()

    async def _scanner_loop(self):
        """Continuously scan watch directories."""
        print("[SCANNER] Starting directory scanner...")

        while self._running:
            try:
                for target in self.watch_dirs:
                    await self._scan_directory(target)

                await asyncio.sleep(self.scan_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[SCANNER] Error: {e}")
                await asyncio.sleep(5)

    async def _ingestion_loop(self):
        """Process pending files into samples."""
        print("[INGEST] Starting ingestion processor...")

        while self._running:
            try:
                # Get file with timeout
                try:
                    file_path = await asyncio.wait_for(
                        self._pending_files.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                # Ingest file
                try:
                    samples = await self.pipeline.ingest_file(file_path)
                    for sample in samples:
                        await self._pending_samples.put(sample)
                        self.stats.samples_ingested += 1

                    if samples:
                        print(f"[INGEST] {file_path.name}: {len(samples)} samples")

                except Exception as e:
                    print(f"[INGEST] Error processing {file_path}: {e}")
                    self._on_error(str(file_path), e)

            except asyncio.CancelledError:
                break

    async def _processing_loop(self):
        """Process samples through the pipeline."""
        print("[PROCESS] Starting sample processor...")

        batch = []
        batch_timeout = 5.0  # Collect samples for up to 5 seconds
        last_batch_time = time.time()

        while self._running:
            try:
                # Collect samples into batch
                try:
                    sample = await asyncio.wait_for(
                        self._pending_samples.get(),
                        timeout=1.0
                    )
                    batch.append(sample)
                except asyncio.TimeoutError:
                    pass

                # Process batch if we have samples and either:
                # 1. Batch is large enough (5+ samples)
                # 2. Timeout reached (5 seconds since first sample)
                should_process = (
                    len(batch) >= 5 or
                    (batch and time.time() - last_batch_time > batch_timeout)
                )

                if should_process and batch:
                    print(f"[PROCESS] Processing batch of {len(batch)} samples...")

                    try:
                        # Tier 1
                        await self.pipeline.process_tier1(batch)

                        # Tier 2 (if enabled and samples passed)
                        if self.tier2_enabled:
                            tier2_samples = [s for s in batch if not s.flagged_for_review]
                            if tier2_samples:
                                print(f"[PROCESS] Tier 2: {len(tier2_samples)} samples")
                                await self.pipeline.process_tier2(tier2_samples)

                        print(f"[PROCESS] Batch complete")

                    except Exception as e:
                        print(f"[PROCESS] Batch error: {e}")
                        for s in batch:
                            self._on_error(s.id, e)

                    batch = []
                    last_batch_time = time.time()

            except asyncio.CancelledError:
                break

    async def _export_loop(self):
        """Periodically export processed data."""
        if not self.auto_export:
            return

        print(f"[EXPORT] Starting auto-exporter (interval: {self.export_interval}s)...")

        while self._running:
            try:
                await asyncio.sleep(self.export_interval)

                # Only export if we have new data
                completed = sum(
                    1 for s in self.pipeline.samples.values()
                    if s.stage == ProcessingStage.COMPLETE
                )

                if completed > self.stats.exports_completed:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

                    # Export corpus
                    corpus_path = self.output_dir / f"corpus_{timestamp}.json"
                    self.pipeline.export_corpus(corpus_path)

                    # Export training data
                    training_path = self.output_dir / f"training_{timestamp}.jsonl"
                    self.pipeline.export_training_data(training_path)

                    self.stats.exports_completed = completed
                    print(f"[EXPORT] Exported {completed} samples to {corpus_path.name}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[EXPORT] Error: {e}")

    async def _health_loop(self):
        """Monitor system health and recover from failures."""
        print("[HEALTH] Starting health monitor...")

        consecutive_failures = 0

        while self._running:
            try:
                await asyncio.sleep(self.health_check_interval)

                # Check Ollama
                try:
                    health = await self.client.health_check()
                    if health['status'] == 'healthy':
                        consecutive_failures = 0
                        if self.state == DaemonState.DEGRADED:
                            print("[HEALTH] Recovered from degraded state")
                            self.state = DaemonState.HEALTHY
                    else:
                        raise Exception("Ollama unhealthy")

                except Exception as e:
                    consecutive_failures += 1
                    print(f"[HEALTH] Check failed ({consecutive_failures}): {e}")

                    if consecutive_failures >= 3:
                        self.state = DaemonState.DEGRADED
                        print("[HEALTH] Entering degraded state, attempting recovery...")
                        await self._attempt_recovery()

                # Check for stale processing
                idle_time = datetime.now() - self.stats.last_activity
                if idle_time > timedelta(minutes=5) and self._pending_samples.qsize() > 0:
                    print(f"[HEALTH] Processing stalled, {self._pending_samples.qsize()} samples pending")

            except asyncio.CancelledError:
                break

    async def _attempt_recovery(self):
        """Attempt to recover from failures."""
        self.state = DaemonState.RECOVERING
        print("[RECOVERY] Attempting system recovery...")

        try:
            # Reinitialize client
            self.client = OllamaClient()
            health = await self.client.health_check()

            if health['status'] == 'healthy':
                # Re-warm model
                await self._warm_model("nemotron-mini:latest")
                self.state = DaemonState.HEALTHY
                print("[RECOVERY] Recovery successful")
            else:
                print("[RECOVERY] Ollama still unhealthy")
                self.state = DaemonState.DEGRADED

        except Exception as e:
            print(f"[RECOVERY] Recovery failed: {e}")
            self.state = DaemonState.DEGRADED

    def _print_status(self):
        """Print current status."""
        stats = self.stats

        print("\n" + "=" * 60)
        print(f"VEX CORPUS DAEMON STATUS - {datetime.now().strftime('%H:%M:%S')}")
        print("=" * 60)
        print(f"State: {self.state.value.upper()}")
        print(f"Uptime: {str(stats.uptime).split('.')[0]}")
        print(f"Throughput: {stats.throughput_per_hour:.1f} samples/hour")
        print("-" * 60)
        print(f"Ingested:  {stats.samples_ingested:>6}")
        print(f"Processed: {stats.samples_processed:>6}")
        print(f"Failed:    {stats.samples_failed:>6}")
        print(f"Exported:  {stats.exports_completed:>6}")
        print("-" * 60)
        print(f"Pending files:   {self._pending_files.qsize():>4}")
        print(f"Pending samples: {self._pending_samples.qsize():>4}")
        print(f"Watch dirs:      {len(self.watch_dirs):>4}")

        if stats.errors:
            print("-" * 60)
            print(f"Recent errors: {len(stats.errors)}")
            for src, msg, ts in stats.errors[-3:]:
                print(f"  [{ts.strftime('%H:%M:%S')}] {src}: {msg[:40]}")

        print("=" * 60 + "\n")

    async def _status_loop(self):
        """Print periodic status updates."""
        while self._running:
            await asyncio.sleep(60)  # Status every minute
            self._print_status()

    async def run(self):
        """Run the autonomous daemon."""
        if not await self.initialize():
            print("[FATAL] Initialization failed, exiting")
            return

        self._running = True

        # Setup signal handlers
        def handle_signal(sig):
            print(f"\n[SIGNAL] Received {sig}, shutting down...")
            self._running = False
            self._shutdown_event.set()

        if sys.platform != 'win32':
            loop = asyncio.get_event_loop()
            loop.add_signal_handler(signal.SIGINT, lambda: handle_signal("SIGINT"))
            loop.add_signal_handler(signal.SIGTERM, lambda: handle_signal("SIGTERM"))

        print("[DAEMON] Starting autonomous processing loops...")
        self._print_status()

        # Create all tasks
        tasks = [
            asyncio.create_task(self._scanner_loop()),
            asyncio.create_task(self._ingestion_loop()),
            asyncio.create_task(self._processing_loop()),
            asyncio.create_task(self._export_loop()),
            asyncio.create_task(self._health_loop()),
            asyncio.create_task(self._status_loop()),
        ]

        try:
            # Wait for shutdown
            await self._shutdown_event.wait()
        except KeyboardInterrupt:
            print("\n[DAEMON] Keyboard interrupt received")
        finally:
            self._running = False
            self.state = DaemonState.STOPPED

            # Cancel all tasks
            for task in tasks:
                task.cancel()

            await asyncio.gather(*tasks, return_exceptions=True)

            # Final export
            if self.auto_export:
                print("[DAEMON] Final export...")
                try:
                    self.pipeline.export_corpus()
                    self.pipeline.export_training_data()
                except Exception as e:
                    print(f"[DAEMON] Final export failed: {e}")

            self._print_status()
            print("[DAEMON] Shutdown complete")

    async def process_once(self, path: Path) -> int:
        """Process a directory once and exit."""
        if not await self.initialize():
            return 1

        print(f"[ONCE] Processing: {path}")

        # Ingest
        if path.is_file():
            samples = await self.pipeline.ingest_file(path)
        else:
            samples = await self.pipeline.ingest_directory(path)

        print(f"[ONCE] Ingested {len(samples)} samples")

        if not samples:
            print("[ONCE] No samples found")
            return 0

        # Process
        await self.pipeline.process_tier1(samples)

        if self.tier2_enabled:
            tier2 = [s for s in samples if not s.flagged_for_review]
            if tier2:
                await self.pipeline.process_tier2(tier2)
        else:
            # Mark samples as complete when skipping Tier 2
            for s in samples:
                if not s.flagged_for_review:
                    s.stage = ProcessingStage.COMPLETE

        # Export
        corpus_path = self.pipeline.export_corpus()
        training_path = self.pipeline.export_training_data()

        print(f"[ONCE] Exported to:")
        print(f"  Corpus:   {corpus_path}")
        print(f"  Training: {training_path}")

        # Stats
        stats = self.pipeline.get_stats()
        print(f"\n[ONCE] Final stats:")
        print(f"  Total:     {stats['total']}")
        print(f"  Complete:  {stats['by_stage'].get('complete', 0)}")
        print(f"  Flagged:   {stats['flagged_for_review']}")
        print(f"  With prompts: {stats['with_prompts']}")

        return 0


async def interactive_mode(daemon: AutonomousDaemon):
    """Run with interactive command input."""
    print("\n[INTERACTIVE] Commands: add <path>, status, export, quit")

    # Start daemon in background
    daemon_task = asyncio.create_task(daemon.run())

    # Simple command loop (non-blocking on Windows is tricky)
    while daemon._running:
        await asyncio.sleep(1)

        # On Windows, we just run until Ctrl+C
        # For full interactive mode, would need proper async stdin handling


def main():
    parser = argparse.ArgumentParser(
        description="VEX Corpus Autonomous Processing Daemon",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python autonomous.py                          # Start daemon with default watch dirs
  python autonomous.py --watch ./vex_files      # Watch specific directory
  python autonomous.py --once ./my_folder       # Process folder once and exit
  python autonomous.py --daemon --no-tier2      # Background mode, Tier 1 only
        """
    )

    parser.add_argument(
        '--watch', '-w',
        action='append',
        type=Path,
        help='Directory to watch for VEX files (can specify multiple)'
    )

    parser.add_argument(
        '--once', '-1',
        type=Path,
        help='Process a path once and exit'
    )

    parser.add_argument(
        '--output', '-o',
        type=Path,
        default=Path("C:/Users/User/vex-corpus/output"),
        help='Output directory for exports'
    )

    parser.add_argument(
        '--daemon', '-d',
        action='store_true',
        help='Run as background daemon (no interactive input)'
    )

    parser.add_argument(
        '--no-tier2',
        action='store_true',
        help='Skip Tier 2 generation (faster, classification only)'
    )

    parser.add_argument(
        '--no-export',
        action='store_true',
        help='Disable automatic periodic exports'
    )

    parser.add_argument(
        '--scan-interval',
        type=float,
        default=10.0,
        help='Directory scan interval in seconds (default: 10)'
    )

    parser.add_argument(
        '--export-interval',
        type=float,
        default=300.0,
        help='Auto-export interval in seconds (default: 300)'
    )

    args = parser.parse_args()

    # Default watch directories if none specified
    watch_dirs = args.watch or [
        Path("C:/Users/User/vex-corpus/input"),
        Path("C:/Users/User/vex-corpus/watch"),
    ]

    # Create any missing directories
    for d in watch_dirs:
        d.mkdir(parents=True, exist_ok=True)

    daemon = AutonomousDaemon(
        watch_dirs=watch_dirs,
        output_dir=args.output,
        scan_interval=args.scan_interval,
        export_interval=args.export_interval,
        auto_export=not args.no_export,
        tier2_enabled=not args.no_tier2,
    )

    # Run mode
    if args.once:
        # Process once and exit
        result = asyncio.run(daemon.process_once(args.once))
        sys.exit(result)
    else:
        # Continuous daemon mode
        try:
            asyncio.run(daemon.run())
        except KeyboardInterrupt:
            print("\nShutdown requested")


if __name__ == "__main__":
    main()
