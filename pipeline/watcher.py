"""File watcher for automatic VEX file ingestion."""

import asyncio
import time
from pathlib import Path
from typing import Callable, Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class FileEvent:
    """A file system event."""
    path: Path
    event_type: str  # created, modified, deleted
    timestamp: datetime


class VEXFileWatcher:
    """Watch directories for new VEX files."""

    EXTENSIONS = {'.vex', '.vfl', '.h', '.md', '.txt', '.json'}

    def __init__(
        self,
        directories: list[Path],
        on_file: Callable[[Path], None],
        poll_interval: float = 2.0,
    ):
        self.directories = directories
        self.on_file = on_file
        self.poll_interval = poll_interval

        self._running = False
        self._seen_files: dict[Path, float] = {}  # path -> mtime
        self._task: Optional[asyncio.Task] = None

    def _scan_directory(self, directory: Path) -> list[FileEvent]:
        """Scan a directory for new/modified files."""
        events = []

        for ext in self.EXTENSIONS:
            for file_path in directory.rglob(f"*{ext}"):
                try:
                    mtime = file_path.stat().st_mtime
                    prev_mtime = self._seen_files.get(file_path)

                    if prev_mtime is None:
                        # New file
                        events.append(FileEvent(
                            path=file_path,
                            event_type="created",
                            timestamp=datetime.now()
                        ))
                    elif mtime > prev_mtime:
                        # Modified file
                        events.append(FileEvent(
                            path=file_path,
                            event_type="modified",
                            timestamp=datetime.now()
                        ))

                    self._seen_files[file_path] = mtime

                except (OSError, PermissionError):
                    continue

        return events

    async def _watch_loop(self):
        """Main watch loop."""
        # Initial scan - just record files without triggering events
        for directory in self.directories:
            if directory.exists():
                for ext in self.EXTENSIONS:
                    for file_path in directory.rglob(f"*{ext}"):
                        try:
                            self._seen_files[file_path] = file_path.stat().st_mtime
                        except (OSError, PermissionError):
                            continue

        # Watch loop
        while self._running:
            for directory in self.directories:
                if not directory.exists():
                    continue

                events = self._scan_directory(directory)

                for event in events:
                    if event.event_type in ("created", "modified"):
                        try:
                            self.on_file(event.path)
                        except Exception:
                            pass

            await asyncio.sleep(self.poll_interval)

    def start(self):
        """Start watching."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._watch_loop())

    def stop(self):
        """Stop watching."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    def add_directory(self, directory: Path):
        """Add a directory to watch."""
        if directory not in self.directories:
            self.directories.append(directory)

    def remove_directory(self, directory: Path):
        """Remove a directory from watch list."""
        if directory in self.directories:
            self.directories.remove(directory)


class BatchAccumulator:
    """Accumulate files into batches before processing."""

    def __init__(
        self,
        on_batch: Callable[[list[Path]], None],
        batch_size: int = 10,
        max_wait: float = 30.0,
    ):
        self.on_batch = on_batch
        self.batch_size = batch_size
        self.max_wait = max_wait

        self._pending: list[Path] = []
        self._last_add: float = 0
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def add(self, path: Path):
        """Add a file to the batch."""
        if path not in self._pending:
            self._pending.append(path)
            self._last_add = time.time()

        # Trigger if batch is full
        if len(self._pending) >= self.batch_size:
            self._flush()

    def _flush(self):
        """Flush pending files to callback."""
        if self._pending:
            batch = self._pending.copy()
            self._pending.clear()
            try:
                self.on_batch(batch)
            except Exception:
                pass

    async def _timer_loop(self):
        """Timer loop to flush after max_wait."""
        while self._running:
            await asyncio.sleep(1.0)

            if self._pending and (time.time() - self._last_add) >= self.max_wait:
                self._flush()

    def start(self):
        """Start the timer."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._timer_loop())

    def stop(self):
        """Stop and flush remaining."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        self._flush()
