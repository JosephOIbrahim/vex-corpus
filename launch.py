#!/usr/bin/env python3
"""
VEX Corpus Unified Launcher

The single entry point for all VEX corpus operations. This launcher:
1. Ensures Ollama is running and models are loaded
2. Validates the environment
3. Launches the appropriate mode (orchestrator, daemon, GUI, etc.)
4. Handles graceful shutdown and final exports

Usage:
    python launch.py                    # Interactive orchestrator (default)
    python launch.py --daemon           # Background autonomous daemon
    python launch.py --gui              # PyQt6 GUI
    python launch.py --once ./folder    # Process folder and exit
    python launch.py --status           # Show system status
    python launch.py --benchmark        # Run model benchmarks
"""

import argparse
import asyncio
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).parent))


# ============================================================================
# Configuration
# ============================================================================

@dataclass
class LaunchConfig:
    """Launch configuration."""
    # Paths
    project_dir: Path = Path(__file__).parent
    output_dir: Path = None
    watch_dirs: list = None

    # Mode
    mode: str = "orchestrator"  # orchestrator, daemon, gui, once, status, benchmark

    # Processing
    enable_tier2: bool = True
    tier1_workers: int = 2
    tier2_workers: int = 1

    # Export
    export_formats: list = None
    auto_export: bool = True
    export_interval: int = 300

    # Ollama
    ollama_url: str = "http://localhost:11434"
    required_models: list = None

    def __post_init__(self):
        self.output_dir = self.output_dir or self.project_dir / "output"
        self.watch_dirs = self.watch_dirs or [
            self.project_dir / "input",
            self.project_dir / "watch",
        ]
        self.export_formats = self.export_formats or ["json", "jsonl"]
        self.required_models = self.required_models or [
            "nemotron-mini",
            "nemotron-3-nano",
        ]


# ============================================================================
# Environment Setup
# ============================================================================

class EnvironmentManager:
    """Manages the runtime environment."""

    def __init__(self, config: LaunchConfig):
        self.config = config

    async def validate(self) -> bool:
        """Validate the environment is ready."""
        print("\n[ENV] Validating environment...")

        # Check Python version
        if sys.version_info < (3, 10):
            print("[ENV] ERROR: Python 3.10+ required")
            return False
        print(f"[ENV] Python {sys.version_info.major}.{sys.version_info.minor} OK")

        # Check required packages
        required = ['httpx', 'pydantic', 'yaml']
        for pkg in required:
            try:
                __import__(pkg)
            except ImportError:
                print(f"[ENV] ERROR: Missing package '{pkg}'. Run: pip install {pkg}")
                return False
        print("[ENV] Required packages OK")

        # Check Ollama
        if not await self._check_ollama():
            return False

        # Check models
        if not await self._check_models():
            return False

        # Create directories
        self._ensure_directories()

        print("[ENV] Environment validation complete\n")
        return True

    async def _check_ollama(self) -> bool:
        """Check if Ollama is running, start if needed."""
        import httpx

        print("[ENV] Checking Ollama...")

        for attempt in range(3):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{self.config.ollama_url}/api/tags",
                        timeout=5.0
                    )
                    if response.status_code == 200:
                        print("[ENV] Ollama is running")
                        return True
            except Exception:
                pass

            if attempt == 0:
                print("[ENV] Ollama not responding, attempting to start...")
                self._start_ollama()
                await asyncio.sleep(3)
            else:
                await asyncio.sleep(2)

        print("[ENV] ERROR: Could not connect to Ollama")
        print("[ENV] Please ensure Ollama is installed and running:")
        print("[ENV]   1. Download from https://ollama.ai")
        print("[ENV]   2. Run 'ollama serve' in a terminal")
        return False

    def _start_ollama(self):
        """Attempt to start Ollama."""
        ollama_paths = [
            "ollama",
            r"C:\Users\User\AppData\Local\Programs\Ollama\ollama.exe",
            r"C:\Program Files\Ollama\ollama.exe",
        ]

        for path in ollama_paths:
            try:
                subprocess.Popen(
                    [path, "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
                )
                print(f"[ENV] Started Ollama from {path}")
                return
            except Exception:
                continue

    async def _check_models(self) -> bool:
        """Check required models are available."""
        import httpx

        print("[ENV] Checking models...")

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.config.ollama_url}/api/tags",
                    timeout=10.0
                )
                data = response.json()
                available = [m['name'] for m in data.get('models', [])]

                missing = []
                for required in self.config.required_models:
                    found = any(required in m for m in available)
                    if not found:
                        missing.append(required)

                if missing:
                    print(f"[ENV] Missing models: {', '.join(missing)}")
                    print("[ENV] Pulling missing models...")

                    for model in missing:
                        await self._pull_model(model)

                nemotron = [m for m in available if 'nemotron' in m.lower()]
                print(f"[ENV] Nemotron models: {', '.join(nemotron)}")
                return True

        except Exception as e:
            print(f"[ENV] ERROR checking models: {e}")
            return False

    async def _pull_model(self, model: str):
        """Pull a model from Ollama."""
        print(f"[ENV] Pulling {model}...")
        try:
            proc = await asyncio.create_subprocess_exec(
                "ollama", "pull", model,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.wait()
            if proc.returncode == 0:
                print(f"[ENV] Pulled {model}")
            else:
                print(f"[ENV] Failed to pull {model}")
        except Exception as e:
            print(f"[ENV] Error pulling {model}: {e}")

    def _ensure_directories(self):
        """Create necessary directories."""
        dirs = [
            self.config.output_dir,
            *self.config.watch_dirs,
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

    async def warm_models(self):
        """Pre-load models into VRAM."""
        import httpx

        models = ["nemotron-mini:latest"]
        print("[ENV] Pre-warming models...")

        for model in models:
            try:
                async with httpx.AsyncClient() as client:
                    await client.post(
                        f"{self.config.ollama_url}/api/generate",
                        json={"model": model, "prompt": "test", "options": {"num_predict": 1}},
                        timeout=60.0
                    )
                print(f"[ENV] Loaded {model}")
            except Exception as e:
                print(f"[ENV] Warning: Could not load {model}: {e}")


# ============================================================================
# Mode Runners
# ============================================================================

async def run_orchestrator(config: LaunchConfig, sources: list[Path], once: bool = False):
    """Run the async orchestrator."""
    from orchestrator import Orchestrator, ExportFormat

    format_map = {
        'json': ExportFormat.JSON_CORPUS,
        'jsonl': ExportFormat.JSONL_TRAINING,
        'chat': ExportFormat.JSONL_CHAT,
        'csv': ExportFormat.CSV_METADATA,
    }

    async with Orchestrator(
        tier1_workers=config.tier1_workers,
        tier2_workers=config.tier2_workers,
        enable_tier2=config.enable_tier2,
    ) as orch:
        # Add sources
        for source in sources:
            await orch.add_source(source)

        # Add export formats
        for fmt in config.export_formats:
            if fmt in format_map:
                orch.add_export_format(format_map[fmt])

        # Run
        if once:
            await orch.run_until_complete()
        else:
            await orch.run_forever()

        # Final export
        await orch.export_all()

        return orch.get_stats()


async def run_daemon(config: LaunchConfig, sources: list[Path]):
    """Run the autonomous daemon."""
    from autonomous import AutonomousDaemon

    daemon = AutonomousDaemon(
        watch_dirs=sources,
        output_dir=config.output_dir,
        auto_export=config.auto_export,
        tier2_enabled=config.enable_tier2,
    )

    await daemon.run()


def run_gui():
    """Run the PyQt6 GUI."""
    from gui.app import main
    main()


async def run_once(config: LaunchConfig, source: Path):
    """Process a single source and exit."""
    from autonomous import AutonomousDaemon

    daemon = AutonomousDaemon(
        output_dir=config.output_dir,
        tier2_enabled=config.enable_tier2,
    )

    return await daemon.process_once(source)


async def show_status(config: LaunchConfig):
    """Show system status."""
    import httpx

    print("\n" + "=" * 60)
    print("VEX CORPUS SYSTEM STATUS")
    print("=" * 60)

    # Ollama status
    print("\n[Ollama]")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{config.ollama_url}/api/tags", timeout=5.0)
            data = response.json()
            models = data.get('models', [])

            print(f"  Status: Running")
            print(f"  Models: {len(models)}")

            nemotron = [m for m in models if 'nemotron' in m['name'].lower()]
            for m in nemotron:
                size_gb = m.get('size', 0) / (1024**3)
                print(f"    - {m['name']} ({size_gb:.1f} GB)")

    except Exception as e:
        print(f"  Status: Not Running ({e})")

    # Output directory
    print("\n[Output]")
    print(f"  Directory: {config.output_dir}")

    if config.output_dir.exists():
        json_files = list(config.output_dir.glob("corpus_*.json"))
        jsonl_files = list(config.output_dir.glob("training_*.jsonl"))

        print(f"  Corpus files: {len(json_files)}")
        print(f"  Training files: {len(jsonl_files)}")

        # Latest corpus stats
        if json_files:
            latest = max(json_files, key=lambda p: p.stat().st_mtime)
            with open(latest) as f:
                data = json.load(f)
            print(f"  Latest: {latest.name}")
            print(f"    Samples: {len(data.get('samples', []))}")

    # Watch directories
    print("\n[Watch Directories]")
    for d in config.watch_dirs:
        exists = "OK" if d.exists() else "MISSING"
        files = len(list(d.glob("*"))) if d.exists() else 0
        print(f"  {d}: {exists} ({files} files)")

    print("\n" + "=" * 60)


async def run_benchmark():
    """Run model benchmarks."""
    print("\n[BENCH] Running model benchmarks...")

    benchmark_path = Path(__file__).parent / "benchmark_models.py"
    if benchmark_path.exists():
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(benchmark_path),
            stdout=None,
            stderr=None
        )
        await proc.wait()
    else:
        print("[BENCH] Benchmark script not found")


# ============================================================================
# Main Entry Point
# ============================================================================

def print_banner():
    """Print startup banner."""
    print("""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   ██╗   ██╗███████╗██╗  ██╗     ██████╗ ██████╗ ██████╗     ║
║   ██║   ██║██╔════╝╚██╗██╔╝    ██╔════╝██╔═══██╗██╔══██╗    ║
║   ██║   ██║█████╗   ╚███╔╝     ██║     ██║   ██║██████╔╝    ║
║   ╚██╗ ██╔╝██╔══╝   ██╔██╗     ██║     ██║   ██║██╔══██╗    ║
║    ╚████╔╝ ███████╗██╔╝ ██╗    ╚██████╗╚██████╔╝██║  ██║    ║
║     ╚═══╝  ╚══════╝╚═╝  ╚═╝     ╚═════╝ ╚═════╝ ╚═╝  ╚═╝    ║
║                                                              ║
║   VEX Corpus Generator - Autonomous Training Data Factory   ║
║   Powered by Nemotron (Local LLM via Ollama)                ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
""")


async def async_main():
    parser = argparse.ArgumentParser(
        description="VEX Corpus Unified Launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python launch.py                      # Interactive orchestrator
  python launch.py --daemon             # Background autonomous daemon
  python launch.py --gui                # PyQt6 GUI
  python launch.py --once ./my_folder   # Process folder and exit
  python launch.py --status             # Show system status
  python launch.py --no-tier2           # Fast mode (classification only)
  python launch.py --export json jsonl chat  # Specify export formats
        """
    )

    # Mode selection
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument('--daemon', '-d', action='store_true',
                           help='Run as autonomous background daemon')
    mode_group.add_argument('--gui', '-g', action='store_true',
                           help='Launch PyQt6 GUI')
    mode_group.add_argument('--once', '-1', type=Path, metavar='PATH',
                           help='Process a path once and exit')
    mode_group.add_argument('--status', '-s', action='store_true',
                           help='Show system status')
    mode_group.add_argument('--benchmark', '-b', action='store_true',
                           help='Run model benchmarks')

    # Sources
    parser.add_argument('sources', nargs='*', type=Path,
                       help='Additional source directories/files')

    # Processing options
    parser.add_argument('--no-tier2', action='store_true',
                       help='Skip Tier 2 generation (faster)')
    parser.add_argument('--tier1-workers', type=int, default=2,
                       help='Number of Tier 1 workers (default: 2)')
    parser.add_argument('--tier2-workers', type=int, default=1,
                       help='Number of Tier 2 workers (default: 1)')

    # Export options
    parser.add_argument('--export', nargs='*',
                       choices=['json', 'jsonl', 'chat', 'csv'],
                       default=['json', 'jsonl'],
                       help='Export formats (default: json jsonl)')
    parser.add_argument('--output', '-o', type=Path,
                       help='Output directory')

    # Misc
    parser.add_argument('--skip-validation', action='store_true',
                       help='Skip environment validation')
    parser.add_argument('--quiet', '-q', action='store_true',
                       help='Minimal output')

    args = parser.parse_args()

    # Build config
    config = LaunchConfig(
        mode="orchestrator",
        enable_tier2=not args.no_tier2,
        tier1_workers=args.tier1_workers,
        tier2_workers=args.tier2_workers,
        export_formats=args.export,
    )

    if args.output:
        config.output_dir = args.output

    # Determine mode
    if args.gui:
        config.mode = "gui"
    elif args.daemon:
        config.mode = "daemon"
    elif args.once:
        config.mode = "once"
    elif args.status:
        config.mode = "status"
    elif args.benchmark:
        config.mode = "benchmark"

    # Print banner (unless quiet or status)
    if not args.quiet and config.mode not in ("status",):
        print_banner()

    # Environment validation (unless skipped)
    if not args.skip_validation and config.mode not in ("status", "gui"):
        env = EnvironmentManager(config)
        if not await env.validate():
            print("\n[LAUNCH] Environment validation failed. Exiting.")
            return 1

        await env.warm_models()

    # Collect sources
    sources = list(args.sources) if args.sources else []
    sources.extend(config.watch_dirs)
    sources = [Path(s) for s in sources]

    # Run appropriate mode
    print(f"\n[LAUNCH] Starting in {config.mode.upper()} mode...\n")

    try:
        if config.mode == "orchestrator":
            await run_orchestrator(config, sources, once=False)

        elif config.mode == "daemon":
            await run_daemon(config, sources)

        elif config.mode == "gui":
            # GUI runs in main thread
            run_gui()

        elif config.mode == "once":
            await run_once(config, args.once)

        elif config.mode == "status":
            await show_status(config)

        elif config.mode == "benchmark":
            await run_benchmark()

        return 0

    except KeyboardInterrupt:
        print("\n[LAUNCH] Interrupted by user")
        return 0
    except Exception as e:
        print(f"\n[LAUNCH] Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


def main():
    """Main entry point."""
    try:
        exit_code = asyncio.run(async_main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(0)


if __name__ == "__main__":
    main()
