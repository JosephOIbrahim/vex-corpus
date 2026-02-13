#!/usr/bin/env python3
"""
VEX Corpus Master Controller

The single unified entry point that combines:
- Orchestrator (async subagent processing)
- Daemon (autonomous file watching)
- Status (live dashboard with real-time updates)

All three run simultaneously in a coordinated system.

Usage:
    python vex.py                     # Full system with live dashboard
    python vex.py ./folder            # Process folder with live dashboard
    python vex.py --headless          # No dashboard, just processing
    python vex.py --export-only       # Just export existing data
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional, Callable
import queue

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    # Enable ANSI escape codes on Windows
    os.system('')

sys.path.insert(0, str(Path(__file__).parent))


# ============================================================================
# ANSI Colors and Terminal Control
# ============================================================================

class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'

    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'

    BG_BLACK = '\033[40m'
    BG_RED = '\033[41m'
    BG_GREEN = '\033[42m'
    BG_BLUE = '\033[44m'

    CLEAR_LINE = '\033[2K'
    CURSOR_UP = '\033[A'
    CURSOR_DOWN = '\033[B'
    CURSOR_HOME = '\033[H'
    CLEAR_SCREEN = '\033[2J'
    HIDE_CURSOR = '\033[?25l'
    SHOW_CURSOR = '\033[?25h'


def colorize(text: str, *colors) -> str:
    """Apply colors to text."""
    return ''.join(colors) + text + Colors.RESET


# ============================================================================
# System State
# ============================================================================

class SystemState(Enum):
    INITIALIZING = "initializing"
    STARTING = "starting"
    RUNNING = "running"
    PROCESSING = "processing"
    IDLE = "idle"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class LiveMetrics:
    """Real-time system metrics."""
    # Timing
    started_at: datetime = field(default_factory=datetime.now)

    # Counts
    files_discovered: int = 0
    samples_ingested: int = 0
    samples_in_tier1: int = 0
    samples_in_tier2: int = 0
    samples_complete: int = 0
    samples_exported: int = 0
    samples_flagged: int = 0

    # Rates (updated periodically)
    ingest_rate: float = 0.0  # samples/min
    process_rate: float = 0.0  # samples/min

    # Queues
    tier1_queue_size: int = 0
    tier2_queue_size: int = 0
    export_queue_size: int = 0

    # Workers
    tier1_workers_active: int = 0
    tier2_workers_active: int = 0

    # Ollama
    ollama_status: str = "unknown"
    current_model: str = ""
    gpu_memory_used: str = ""

    # Houdini
    houdini_enabled: bool = False
    houdini_connected: bool = False
    houdini_port: int = 9008
    houdini_samples_pulled: int = 0

    # Errors
    error_count: int = 0
    last_error: str = ""

    # Activity log (last N events)
    activity_log: list = field(default_factory=list)

    @property
    def uptime(self) -> timedelta:
        return datetime.now() - self.started_at

    @property
    def uptime_str(self) -> str:
        td = self.uptime
        hours, remainder = divmod(int(td.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def log_activity(self, msg: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.activity_log.append(f"[{timestamp}] {msg}")
        # Keep last 20 entries
        if len(self.activity_log) > 20:
            self.activity_log = self.activity_log[-20:]


# ============================================================================
# Live Dashboard
# ============================================================================

class LiveDashboard:
    """Real-time terminal dashboard."""

    def __init__(self, metrics: LiveMetrics):
        self.metrics = metrics
        self.state = SystemState.INITIALIZING
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._refresh_rate = 0.5  # seconds
        self._width = 70

    def start(self):
        """Start the dashboard refresh loop."""
        self._running = True
        self._thread = threading.Thread(target=self._refresh_loop, daemon=True)
        self._thread.start()
        print(Colors.HIDE_CURSOR, end='', flush=True)

    def stop(self):
        """Stop the dashboard."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        print(Colors.SHOW_CURSOR, end='', flush=True)

    def _refresh_loop(self):
        """Background refresh loop."""
        while self._running:
            try:
                self._render()
                time.sleep(self._refresh_rate)
            except Exception:
                pass

    def _render(self):
        """Render the dashboard."""
        m = self.metrics
        w = self._width

        lines = []

        # Header
        lines.append(Colors.CLEAR_SCREEN + Colors.CURSOR_HOME)
        lines.append(self._header())
        lines.append("")

        # State indicator
        state_color = {
            SystemState.RUNNING: Colors.GREEN,
            SystemState.PROCESSING: Colors.CYAN,
            SystemState.IDLE: Colors.YELLOW,
            SystemState.ERROR: Colors.RED,
        }.get(self.state, Colors.WHITE)

        lines.append(f"  {colorize('STATE:', Colors.BOLD)} {colorize(self.state.value.upper(), state_color, Colors.BOLD)}    {colorize('UPTIME:', Colors.BOLD)} {m.uptime_str}")
        lines.append("")

        # Metrics section
        lines.append(self._section_header("PIPELINE METRICS"))

        # Progress bars
        total = max(m.samples_ingested, 1)
        tier1_pct = min(100, int((m.samples_in_tier1 + m.samples_complete) / total * 100)) if total > 0 else 0
        tier2_pct = min(100, int((m.samples_in_tier2 + m.samples_complete) / total * 100)) if total > 0 else 0
        complete_pct = min(100, int(m.samples_complete / total * 100)) if total > 0 else 0

        lines.append(f"  {colorize('Ingested:', Colors.CYAN)}    {m.samples_ingested:>6}")
        lines.append(f"  {colorize('Tier 1:', Colors.YELLOW)}      {m.samples_in_tier1:>6}  {self._progress_bar(tier1_pct, 30)}")
        lines.append(f"  {colorize('Tier 2:', Colors.MAGENTA)}      {m.samples_in_tier2:>6}  {self._progress_bar(tier2_pct, 30)}")
        lines.append(f"  {colorize('Complete:', Colors.GREEN)}    {m.samples_complete:>6}  {self._progress_bar(complete_pct, 30)}")
        lines.append(f"  {colorize('Exported:', Colors.BLUE)}    {m.samples_exported:>6}")
        if m.samples_flagged:
            lines.append(f"  {colorize('Flagged:', Colors.RED)}     {m.samples_flagged:>6}")
        lines.append("")

        # Queues and workers
        lines.append(self._section_header("WORKERS & QUEUES"))
        lines.append(f"  Tier1: {colorize(str(m.tier1_workers_active), Colors.GREEN)} active    Queue: {m.tier1_queue_size:>4}")
        lines.append(f"  Tier2: {colorize(str(m.tier2_workers_active), Colors.GREEN)} active    Queue: {m.tier2_queue_size:>4}")
        lines.append(f"  Export:              Queue: {m.export_queue_size:>4}")
        lines.append("")

        # Throughput
        lines.append(self._section_header("THROUGHPUT"))
        lines.append(f"  Ingest:  {colorize(f'{m.ingest_rate:.1f}', Colors.CYAN)} samples/min")
        lines.append(f"  Process: {colorize(f'{m.process_rate:.1f}', Colors.GREEN)} samples/min")
        lines.append("")

        # Ollama status
        lines.append(self._section_header("OLLAMA"))
        ollama_color = Colors.GREEN if m.ollama_status == "healthy" else Colors.RED
        lines.append(f"  Status: {colorize(m.ollama_status, ollama_color)}")
        if m.current_model:
            lines.append(f"  Model:  {m.current_model}")
        lines.append("")

        # Houdini status (if enabled)
        if m.houdini_enabled:
            lines.append(self._section_header("HOUDINI"))
            houdini_color = Colors.GREEN if m.houdini_connected else Colors.YELLOW
            houdini_status = "connected" if m.houdini_connected else "waiting..."
            lines.append(f"  Port:   {m.houdini_port}  Status: {colorize(houdini_status, houdini_color)}")
            lines.append(f"  Pulled: {m.houdini_samples_pulled} samples")
            lines.append("")

        # Activity log
        lines.append(self._section_header("ACTIVITY LOG"))
        log_lines = m.activity_log[-8:] if m.activity_log else ["  (no activity)"]
        for entry in log_lines:
            lines.append(f"  {colorize(entry, Colors.DIM)}")
        lines.append("")

        # Footer
        lines.append(self._footer())

        # Print all at once to reduce flicker
        print('\n'.join(lines), end='', flush=True)

    def _header(self) -> str:
        title = " VEX CORPUS MASTER CONTROLLER "
        padding = (self._width - len(title)) // 2
        return colorize("=" * padding + title + "=" * padding, Colors.CYAN, Colors.BOLD)

    def _footer(self) -> str:
        hint = " Press Ctrl+C to stop "
        padding = (self._width - len(hint)) // 2
        return colorize("-" * padding + hint + "-" * padding, Colors.DIM)

    def _section_header(self, title: str) -> str:
        return colorize(f"  [{title}]", Colors.BOLD)

    def _progress_bar(self, percent: int, width: int = 20) -> str:
        filled = int(width * percent / 100)
        empty = width - filled
        bar = "█" * filled + "░" * empty

        if percent >= 100:
            color = Colors.GREEN
        elif percent >= 50:
            color = Colors.YELLOW
        else:
            color = Colors.CYAN

        return f"{colorize(bar, color)} {percent:>3}%"


# ============================================================================
# Master Controller
# ============================================================================

class MasterController:
    """
    Unified controller that runs orchestrator, daemon, and status simultaneously.
    """

    def __init__(
        self,
        sources: list[Path] = None,
        output_dir: Path = None,
        enable_tier2: bool = True,
        headless: bool = False,
        tier1_workers: int = 2,
        tier2_workers: int = 1,
        enable_houdini: bool = False,
        houdini_port: int = 9008,
    ):
        self.sources = sources or [
            Path("C:/Users/User/vex-corpus/input"),
            Path("C:/Users/User/vex-corpus/watch"),
        ]
        self.output_dir = output_dir or Path("C:/Users/User/vex-corpus/output")
        self.enable_tier2 = enable_tier2
        self.headless = headless
        self.tier1_workers = tier1_workers
        self.tier2_workers = tier2_workers
        self.enable_houdini = enable_houdini
        self.houdini_port = houdini_port

        # State
        self.metrics = LiveMetrics()
        self.dashboard: Optional[LiveDashboard] = None
        self._running = False
        self._shutdown_event = asyncio.Event()

        # Components (lazy loaded)
        self._client = None
        self._pipeline = None
        self._orchestrator = None

    async def initialize(self) -> bool:
        """Initialize all components."""
        self.metrics.log_activity("Initializing system...")

        try:
            # Check/start Ollama
            if not await self._ensure_ollama():
                return False

            # Check models
            if not await self._ensure_models():
                return False

            # Pre-warm model
            await self._warm_model()

            # Create directories
            self.output_dir.mkdir(parents=True, exist_ok=True)
            for s in self.sources:
                s.mkdir(parents=True, exist_ok=True)

            self.metrics.log_activity("Initialization complete")
            return True

        except Exception as e:
            self.metrics.last_error = str(e)
            self.metrics.error_count += 1
            self.metrics.log_activity(f"Init error: {e}")
            return False

    async def _ensure_ollama(self) -> bool:
        """Ensure Ollama is running."""
        import httpx

        self.metrics.log_activity("Checking Ollama...")

        for attempt in range(5):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        "http://localhost:11434/api/tags",
                        timeout=5.0
                    )
                    if response.status_code == 200:
                        self.metrics.ollama_status = "healthy"
                        self.metrics.log_activity("Ollama connected")
                        return True
            except Exception:
                pass

            if attempt == 0:
                self.metrics.log_activity("Starting Ollama...")
                self._start_ollama()

            await asyncio.sleep(2)

        self.metrics.ollama_status = "failed"
        self.metrics.log_activity("Ollama connection failed")
        return False

    def _start_ollama(self):
        """Try to start Ollama."""
        paths = [
            "ollama",
            r"C:\Users\User\AppData\Local\Programs\Ollama\ollama.exe",
        ]
        for path in paths:
            try:
                subprocess.Popen(
                    [path, "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
                )
                return
            except Exception:
                continue

    async def _ensure_models(self) -> bool:
        """Check required models."""
        import httpx

        self.metrics.log_activity("Checking models...")

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get("http://localhost:11434/api/tags", timeout=10.0)
                data = response.json()
                models = [m['name'] for m in data.get('models', [])]

                nemotron = [m for m in models if 'nemotron' in m.lower()]
                if not nemotron:
                    self.metrics.log_activity("No Nemotron models - please pull")
                    return False

                self.metrics.log_activity(f"Found {len(nemotron)} Nemotron models")
                return True

        except Exception as e:
            self.metrics.log_activity(f"Model check failed: {e}")
            return False

    async def _warm_model(self):
        """Pre-warm the primary model."""
        import httpx

        model = "nemotron-mini:latest"
        self.metrics.log_activity(f"Loading {model}...")
        self.metrics.current_model = model

        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    "http://localhost:11434/api/generate",
                    json={"model": model, "prompt": "test", "options": {"num_predict": 1}},
                    timeout=60.0
                )
            self.metrics.log_activity(f"{model} loaded")
        except Exception as e:
            self.metrics.log_activity(f"Warm failed: {e}")

    async def run(self):
        """Run the master controller."""
        # Initialize
        if not await self.initialize():
            print("\nInitialization failed. Check the logs above.")
            return

        self._running = True

        # Start dashboard if not headless
        if not self.headless:
            self.dashboard = LiveDashboard(self.metrics)
            self.dashboard.start()

        try:
            # Import and create orchestrator
            from orchestrator import Orchestrator, ExportFormat

            self._orchestrator = Orchestrator(
                tier1_workers=self.tier1_workers,
                tier2_workers=self.tier2_workers if self.enable_tier2 else 0,
                enable_tier2=self.enable_tier2,
                enable_houdini=self.enable_houdini,
                houdini_port=self.houdini_port,
            )

            # Initialize orchestrator
            await self._orchestrator.initialize()

            # Add sources
            for source in self.sources:
                await self._orchestrator.add_source(source)

            # Start orchestrator
            await self._orchestrator.start()

            if self.dashboard:
                self.dashboard.state = SystemState.RUNNING

            self.metrics.log_activity("System running")

            # Main loop - update metrics from orchestrator
            stable_count = 0
            last_state = (0, 0)

            # Wait for initial ingestion
            await asyncio.sleep(3.0)

            while self._running:
                await asyncio.sleep(0.5)

                # Sync metrics from orchestrator
                self._sync_metrics()

                # Check for keyboard interrupt handled by signal
                if self._shutdown_event.is_set():
                    break

                # In headless mode, check for completion
                if self.headless:
                    m = self.metrics
                    current_state = (m.samples_ingested, m.samples_complete)

                    if current_state == last_state:
                        stable_count += 1
                    else:
                        stable_count = 0
                        last_state = current_state

                    # All queues empty, all samples processed, stable for 5s
                    all_done = (
                        m.tier1_queue_size == 0 and
                        m.tier2_queue_size == 0 and
                        m.export_queue_size == 0 and
                        m.samples_ingested > 0 and
                        m.samples_complete >= m.samples_ingested - m.samples_flagged and
                        stable_count >= 10
                    )

                    if all_done:
                        self.metrics.log_activity("All processing complete")
                        break

        except KeyboardInterrupt:
            pass
        except Exception as e:
            self.metrics.error_count += 1
            self.metrics.last_error = str(e)
            self.metrics.log_activity(f"Error: {e}")
        finally:
            await self.shutdown()

    def _sync_metrics(self):
        """Sync metrics from orchestrator."""
        if not self._orchestrator:
            return

        o = self._orchestrator
        m = self.metrics

        # Update counts
        m.samples_ingested = o.metrics.total_ingested
        m.samples_complete = o.metrics.total_processed
        m.samples_exported = o.metrics.total_exported

        # Queue sizes
        m.tier1_queue_size = o.tier1_queue.qsize()
        m.tier2_queue_size = o.tier2_queue.qsize()
        m.export_queue_size = o.export_queue.qsize()

        # Count by stage
        m.samples_in_tier1 = sum(
            1 for s in o.samples.values()
            if s.stage.value == "tier1_classification"
        )
        m.samples_in_tier2 = sum(
            1 for s in o.samples.values()
            if s.stage.value == "tier2_generation"
        )
        m.samples_flagged = sum(
            1 for s in o.samples.values()
            if s.flagged_for_review
        )

        # Worker counts
        m.tier1_workers_active = sum(
            1 for a in o.tier1_agents
            if a.state.value == 2  # RUNNING
        )
        m.tier2_workers_active = sum(
            1 for a in o.tier2_agents
            if a.state.value == 2
        )

        # Houdini status
        m.houdini_enabled = o.enable_houdini
        m.houdini_port = o.houdini_port
        if o.houdini_agent:
            m.houdini_connected = getattr(o.houdini_agent.bridge, '_connected', False) if o.houdini_agent.bridge else False
            m.houdini_samples_pulled = o.houdini_agent.samples_pulled

        # Calculate rates
        elapsed_minutes = m.uptime.total_seconds() / 60
        if elapsed_minutes > 0.1:
            m.ingest_rate = m.samples_ingested / elapsed_minutes
            m.process_rate = m.samples_complete / elapsed_minutes

        # Update dashboard state
        if self.dashboard:
            if m.tier1_queue_size > 0 or m.tier2_queue_size > 0:
                self.dashboard.state = SystemState.PROCESSING
            elif m.samples_ingested > 0:
                self.dashboard.state = SystemState.IDLE
            else:
                self.dashboard.state = SystemState.RUNNING

    async def shutdown(self):
        """Graceful shutdown."""
        self._running = False

        if self.dashboard:
            self.dashboard.state = SystemState.STOPPING

        self.metrics.log_activity("Shutting down...")

        # Stop orchestrator
        if self._orchestrator:
            await self._orchestrator.shutdown()

        # Final export
        await self._final_export()

        # Stop dashboard
        if self.dashboard:
            self.dashboard.state = SystemState.STOPPED
            await asyncio.sleep(1)  # Let dashboard show final state
            self.dashboard.stop()

        # Print final summary
        self._print_summary()

    async def _final_export(self):
        """Export all remaining data."""
        if not self._orchestrator:
            return

        self.metrics.log_activity("Final export...")

        try:
            await self._orchestrator.export_agent.export_all()
            self.metrics.log_activity("Export complete")
        except Exception as e:
            self.metrics.log_activity(f"Export failed: {e}")

    def _print_summary(self):
        """Print final summary."""
        m = self.metrics

        print("\n")
        print("=" * 60)
        print("  SESSION SUMMARY")
        print("=" * 60)
        print(f"  Duration:    {m.uptime_str}")
        print(f"  Ingested:    {m.samples_ingested}")
        print(f"  Processed:   {m.samples_complete}")
        print(f"  Exported:    {m.samples_exported}")
        print(f"  Flagged:     {m.samples_flagged}")
        print(f"  Errors:      {m.error_count}")
        print("-" * 60)
        print(f"  Output:      {self.output_dir}")
        print("=" * 60)
        print()


# ============================================================================
# CLI
# ============================================================================

def print_banner():
    banner = """
╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║   ██╗   ██╗███████╗██╗  ██╗    ██████╗ ██████╗ ██████╗ ██████╗  ║
║   ██║   ██║██╔════╝╚██╗██╔╝   ██╔════╝██╔═══██╗██╔══██╗██╔══██╗ ║
║   ██║   ██║█████╗   ╚███╔╝    ██║     ██║   ██║██████╔╝██████╔╝ ║
║   ╚██╗ ██╔╝██╔══╝   ██╔██╗    ██║     ██║   ██║██╔══██╗██╔═══╝  ║
║    ╚████╔╝ ███████╗██╔╝ ██╗   ╚██████╗╚██████╔╝██║  ██║██║      ║
║     ╚═══╝  ╚══════╝╚═╝  ╚═╝    ╚═════╝ ╚═════╝ ╚═╝  ╚═╝╚═╝      ║
║                                                                  ║
║   Master Controller - Autonomous VEX Training Data Factory      ║
║   Powered by Nemotron (Local LLM via Ollama)                    ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
"""
    print(colorize(banner, Colors.CYAN))


async def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="VEX Corpus Master Controller",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python vex.py                       # Full system with live dashboard
  python vex.py ./my_vex_files        # Watch specific folder
  python vex.py --headless            # No dashboard, just processing
  python vex.py --fast                # Tier 1 only (no prompt generation)
  python vex.py --workers 4           # More parallel workers
        """
    )

    parser.add_argument('sources', nargs='*', type=Path,
                       help='Source directories/files to process')

    parser.add_argument('--headless', '-H', action='store_true',
                       help='Run without live dashboard')

    parser.add_argument('--fast', '-f', action='store_true',
                       help='Fast mode - Tier 1 only (no prompt generation)')

    parser.add_argument('--workers', '-w', type=int, default=2,
                       help='Number of Tier 1 workers (default: 2)')

    parser.add_argument('--output', '-o', type=Path,
                       help='Output directory')

    parser.add_argument('--quiet', '-q', action='store_true',
                       help='Minimal output (implies --headless)')

    parser.add_argument('--houdini', action='store_true',
                       help='Enable Houdini integration (pulls VEX from running Houdini)')

    parser.add_argument('--houdini-port', type=int, default=18811,
                       help='Houdini hrpyc port (default: 18811)')

    args = parser.parse_args()

    # Print banner unless quiet
    if not args.quiet:
        print_banner()

    # Build source list
    sources = list(args.sources) if args.sources else None

    # Create controller
    controller = MasterController(
        sources=sources,
        output_dir=args.output,
        enable_tier2=not args.fast,
        headless=args.headless or args.quiet,
        tier1_workers=args.workers,
        tier2_workers=1 if not args.fast else 0,
        enable_houdini=args.houdini,
        houdini_port=args.houdini_port,
    )

    # Setup signal handler
    def signal_handler(sig, frame):
        controller._shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, signal_handler)

    # Run
    await controller.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nInterrupted.")
