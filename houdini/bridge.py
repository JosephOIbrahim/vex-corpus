#!/usr/bin/env python3
"""
Houdini Bridge - VEX Corpus Integration

Connects to Houdini via hrpyc (Houdini Remote Python Connection) for
bidirectional VEX code exchange.

Houdini 21 uses hrpyc instead of the old command port:
    - Default port: 18811
    - Alternative: 9008 (if configured)

Usage:
    # In Houdini Python Shell, start the RPC server:
    import hrpyc
    hrpyc.start_server()  # Default port 18811
    # Or: hrpyc.start_server(port=9008)

    # Then from VEX Corpus:
    python -m houdini.bridge --pull-all
    python -m houdini.bridge --watch
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Optional, Callable, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


# ============================================================================
# Houdini Connection via hrpyc
# ============================================================================

class HoudiniConnectionError(Exception):
    """Failed to connect to Houdini."""
    pass


class HoudiniConnection:
    """
    Connection to Houdini via hrpyc (Remote Python).

    Houdini 21+ uses hrpyc instead of the old commandport.
    The hrpyc module wraps the rpyc library for remote procedure calls.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 18811):
        self.host = host
        self.port = port
        self._connection = None
        self._hou = None  # Remote hou module
        self._connected = False

    def connect(self) -> bool:
        """Establish connection to Houdini via hrpyc."""
        try:
            import rpyc
        except ImportError:
            raise HoudiniConnectionError(
                "rpyc module not installed. Install with: pip install rpyc"
            )

        try:
            # Connect to Houdini's hrpyc server
            self._connection = rpyc.classic.connect(self.host, self.port)

            # Import remote hou module
            self._hou = self._connection.modules.hou

            # Test the connection
            _ = self._hou.applicationVersion()

            self._connected = True
            print(f"[HOUDINI] Connected via hrpyc at {self.host}:{self.port}")
            return True

        except ConnectionRefusedError:
            raise HoudiniConnectionError(
                f"Cannot connect to Houdini at {self.host}:{self.port}.\n"
                f"Make sure Houdini is running with hrpyc server enabled:\n"
                f"  In Houdini Python Shell:\n"
                f"    import hrpyc\n"
                f"    hrpyc.start_server(port={self.port})"
            )
        except Exception as e:
            raise HoudiniConnectionError(f"Connection failed: {e}")

    def disconnect(self):
        """Close the connection."""
        if self._connection:
            try:
                self._connection.close()
            except:
                pass
        self._connection = None
        self._hou = None
        self._connected = False

    @property
    def hou(self):
        """Get the remote hou module."""
        if not self._connected:
            raise HoudiniConnectionError("Not connected to Houdini")
        return self._hou

    def execute(self, code: str) -> Any:
        """Execute Python code in Houdini and return result."""
        if not self._connected:
            raise HoudiniConnectionError("Not connected to Houdini")

        # Execute in Houdini's namespace
        return self._connection.execute(code)


# ============================================================================
# VEX Extraction
# ============================================================================

@dataclass
class HoudiniVEXNode:
    """Represents a VEX-containing node in Houdini."""
    path: str                    # Full node path, e.g., /obj/geo1/attribwrangle1
    name: str                    # Node name
    node_type: str               # e.g., "attribwrangle", "volumewrangle"
    context: str                 # "points", "prims", "vertices", "detail"
    vex_code: str                # The actual VEX code
    parameters: dict = field(default_factory=dict)


class HoudiniVEXExtractor:
    """
    Extracts VEX code from nodes in a Houdini scene.

    Supported node types:
    - attribwrangle (Point/Prim/Vertex/Detail Wrangle)
    - volumewrangle
    - popwrangle
    - geometryvop (inline VEX)
    """

    WRANGLE_TYPES = ["attribwrangle", "volumewrangle", "popwrangle", "gaswrangle"]

    def __init__(self, connection: HoudiniConnection):
        self.conn = connection

    def _get_context(self, node) -> str:
        """Determine wrangle context from class parameter."""
        try:
            cls_parm = node.parm("class")
            if cls_parm:
                val = cls_parm.evalAsInt()
                return {0: "detail", 1: "points", 2: "prims", 3: "vertices"}.get(val, "points")
        except:
            pass
        return "points"

    def find_all_wrangles(self) -> list[HoudiniVEXNode]:
        """Find all wrangle nodes in the current scene."""
        hou = self.conn.hou
        results = []

        try:
            root = hou.node("/")
            for node in root.allSubChildren():
                try:
                    type_name = node.type().name()

                    if any(wt in type_name for wt in self.WRANGLE_TYPES):
                        snippet_parm = node.parm("snippet")
                        if snippet_parm:
                            code = snippet_parm.evalAsString()
                            if code and code.strip():
                                results.append(HoudiniVEXNode(
                                    path=node.path(),
                                    name=node.name(),
                                    node_type=type_name,
                                    context=self._get_context(node),
                                    vex_code=code
                                ))
                except Exception:
                    continue

        except Exception as e:
            print(f"[HOUDINI] Error finding wrangles: {e}")

        return results

    def get_node_vex(self, node_path: str) -> Optional[HoudiniVEXNode]:
        """Get VEX code from a specific node."""
        hou = self.conn.hou

        try:
            node = hou.node(node_path)
            if node:
                snippet = node.parm("snippet")
                if snippet:
                    return HoudiniVEXNode(
                        path=node.path(),
                        name=node.name(),
                        node_type=node.type().name(),
                        context=self._get_context(node),
                        vex_code=snippet.evalAsString()
                    )
        except Exception as e:
            print(f"[HOUDINI] Error getting node VEX: {e}")

        return None

    def set_node_vex(self, node_path: str, vex_code: str) -> bool:
        """Set VEX code on a specific node."""
        hou = self.conn.hou

        try:
            node = hou.node(node_path)
            if node:
                snippet = node.parm("snippet")
                if snippet:
                    snippet.set(vex_code)
                    return True
        except Exception as e:
            print(f"[HOUDINI] Error setting node VEX: {e}")

        return False


# ============================================================================
# Houdini Bridge (High-Level Interface)
# ============================================================================

class HoudiniBridge:
    """
    High-level interface between VEX Corpus and Houdini.

    Provides:
    - Pull VEX from all wrangles in scene
    - Push VEX to specific nodes
    - Watch for changes (polling)
    - Queue samples directly to the corpus pipeline
    """

    def __init__(self,
                 host: str = "127.0.0.1",
                 port: int = 18811,
                 corpus_callback: Optional[Callable] = None):
        """
        Initialize bridge.

        Args:
            host: Houdini host (usually localhost)
            port: hrpyc port (default 18811)
            corpus_callback: Called with VEXSample when new code is found
        """
        self.host = host
        self.port = port
        self.corpus_callback = corpus_callback

        self._conn: Optional[HoudiniConnection] = None
        self._extractor: Optional[HoudiniVEXExtractor] = None
        self._connected = False
        self._watching = False

        # Track seen code to avoid duplicates
        self._seen_hashes: set[str] = set()
        self._node_versions: dict[str, str] = {}  # path -> hash of code

    def connect(self) -> bool:
        """Connect to Houdini."""
        try:
            self._conn = HoudiniConnection(self.host, self.port)
            self._conn.connect()
            self._extractor = HoudiniVEXExtractor(self._conn)
            self._connected = True
            return True
        except HoudiniConnectionError as e:
            print(f"[HOUDINI] {e}")
            self._connected = False
            return False

    def disconnect(self):
        """Disconnect from Houdini."""
        if self._conn:
            self._conn.disconnect()
        self._connected = False
        self._watching = False
        print("[HOUDINI] Disconnected")

    def is_connected(self) -> bool:
        """Check if connected to Houdini."""
        return self._connected

    def pull_all_vex(self) -> list[HoudiniVEXNode]:
        """Pull all VEX code from the current Houdini scene."""
        if not self._connected:
            print("[HOUDINI] Not connected")
            return []

        nodes = self._extractor.find_all_wrangles()
        print(f"[HOUDINI] Found {len(nodes)} wrangle nodes")
        return nodes

    def pull_node(self, node_path: str) -> Optional[HoudiniVEXNode]:
        """Pull VEX from a specific node."""
        if not self._connected:
            return None
        return self._extractor.get_node_vex(node_path)

    def push_vex(self, node_path: str, vex_code: str) -> bool:
        """Push VEX code to a specific node."""
        if not self._connected:
            return False
        return self._extractor.set_node_vex(node_path, vex_code)

    def _hash_code(self, code: str) -> str:
        """Generate hash for deduplication."""
        import hashlib
        return hashlib.sha256(code.encode()).hexdigest()[:16]

    def ingest_to_corpus(self, nodes: list[HoudiniVEXNode] = None):
        """
        Send VEX nodes to the corpus for processing.

        If no nodes provided, pulls all from Houdini.
        """
        if nodes is None:
            nodes = self.pull_all_vex()

        if not nodes:
            print("[HOUDINI] No VEX nodes to ingest")
            return

        # Import here to avoid circular dependency
        from pipeline.intake import VEXSample, ProcessingStage

        new_samples = []
        for node in nodes:
            code_hash = self._hash_code(node.vex_code)

            if code_hash in self._seen_hashes:
                continue

            self._seen_hashes.add(code_hash)

            sample = VEXSample(
                id=f"houdini_{code_hash}",
                code=node.vex_code,
                source_file=f"houdini://{node.path}",
                source_line=0,
                hash=code_hash,
                context=node.context,
                stage=ProcessingStage.PENDING
            )

            new_samples.append(sample)

            if self.corpus_callback:
                self.corpus_callback(sample)

        print(f"[HOUDINI] Ingested {len(new_samples)} new samples to corpus")
        return new_samples

    async def watch(self, interval: float = 2.0):
        """
        Watch Houdini for VEX changes and auto-ingest.

        Polls every `interval` seconds for new/modified wrangles.
        """
        if not self._connected:
            print("[HOUDINI] Not connected - cannot watch")
            return

        self._watching = True
        print(f"[HOUDINI] Watching for changes (every {interval}s)...")

        while self._watching:
            try:
                nodes = self.pull_all_vex()

                for node in nodes:
                    code_hash = self._hash_code(node.vex_code)

                    # Check if this node's code changed
                    if self._node_versions.get(node.path) != code_hash:
                        self._node_versions[node.path] = code_hash

                        if code_hash not in self._seen_hashes:
                            self._seen_hashes.add(code_hash)

                            print(f"[HOUDINI] New/modified: {node.path}")

                            # Ingest single node
                            self.ingest_to_corpus([node])

            except Exception as e:
                print(f"[HOUDINI] Watch error: {e}")

            await asyncio.sleep(interval)

    def stop_watching(self):
        """Stop the watch loop."""
        self._watching = False


# ============================================================================
# Async Subagent for Orchestrator Integration
# ============================================================================

class HoudiniSubagent:
    """
    Async subagent that integrates with the VEX Corpus orchestrator.

    Runs alongside other subagents, feeding samples from Houdini
    directly into the processing pipeline.
    """

    def __init__(self, orchestrator, host: str = "127.0.0.1", port: int = 18811):
        self.orchestrator = orchestrator
        self.host = host
        self.port = port
        self.bridge: Optional[HoudiniBridge] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # Metrics
        self.samples_pulled = 0
        self.last_pull = None

    async def start(self):
        """Start the Houdini subagent."""
        self._running = True

        # Create bridge with callback to orchestrator
        self.bridge = HoudiniBridge(
            host=self.host,
            port=self.port,
            corpus_callback=self._on_sample
        )

        if not self.bridge.connect():
            print("[HOUDINI-AGENT] Could not connect - will retry periodically")

        self._task = asyncio.create_task(self._run())

    async def stop(self):
        """Stop the subagent."""
        self._running = False

        if self.bridge:
            self.bridge.stop_watching()
            self.bridge.disconnect()

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self):
        """Main run loop - watch for Houdini changes."""
        reconnect_interval = 10.0
        watch_interval = 2.0

        while self._running:
            try:
                if not self.bridge.is_connected():
                    # Try to connect
                    if self.bridge.connect():
                        print("[HOUDINI-AGENT] Connected!")
                    else:
                        await asyncio.sleep(reconnect_interval)
                        continue

                # Poll for changes
                nodes = self.bridge.pull_all_vex()

                for node in nodes:
                    code_hash = self.bridge._hash_code(node.vex_code)

                    if code_hash not in self.bridge._seen_hashes:
                        self.bridge._seen_hashes.add(code_hash)
                        self._queue_sample(node, code_hash)
                        self.samples_pulled += 1
                        self.last_pull = datetime.now()

                await asyncio.sleep(watch_interval)

            except Exception as e:
                print(f"[HOUDINI-AGENT] Error: {e}")
                # Mark as disconnected on error
                if self.bridge:
                    self.bridge._connected = False
                await asyncio.sleep(reconnect_interval)

    def _on_sample(self, sample):
        """Callback when bridge finds a sample."""
        # Queue to orchestrator
        if hasattr(self.orchestrator, 'tier1_queue'):
            asyncio.create_task(self.orchestrator.tier1_queue.put(sample))
            self.orchestrator.metrics.total_ingested += 1

    def _queue_sample(self, node: HoudiniVEXNode, code_hash: str):
        """Queue a sample to the orchestrator."""
        from pipeline.intake import VEXSample, ProcessingStage

        sample = VEXSample(
            id=f"houdini_{code_hash}",
            code=node.vex_code,
            source_file=f"houdini://{node.path}",
            source_line=0,
            hash=code_hash,
            context=node.context,
            stage=ProcessingStage.PENDING
        )

        # Add to orchestrator tracking
        if hasattr(self.orchestrator, 'samples'):
            self.orchestrator.samples[sample.id] = sample
            self.orchestrator.seen_hashes.add(code_hash)

        # Queue for processing
        asyncio.create_task(self.orchestrator.tier1_queue.put(sample))

        if hasattr(self.orchestrator, 'metrics'):
            self.orchestrator.metrics.total_ingested += 1

        print(f"[HOUDINI-AGENT] Queued: {node.path} ({node.context})")


# ============================================================================
# CLI Interface
# ============================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Houdini VEX Bridge - Connect VEX Corpus to Houdini",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Pull all VEX from Houdini (default port 18811)
  python -m houdini.bridge --pull-all

  # Pull from alternative port
  python -m houdini.bridge --port 9008 --pull-all

  # Pull specific node
  python -m houdini.bridge --pull /obj/geo1/attribwrangle1

  # Watch for changes and auto-ingest
  python -m houdini.bridge --watch

  # Push VEX to a node
  python -m houdini.bridge --push /obj/geo1/wrangle1 --code "@P.y += 1;"

Setup in Houdini (Python Shell):
  import hrpyc
  hrpyc.start_server()           # Default port 18811
  hrpyc.start_server(port=9008)  # Custom port
        """
    )

    parser.add_argument('--host', default='127.0.0.1', help='Houdini host')
    parser.add_argument('--port', type=int, default=18811, help='hrpyc port (default 18811)')
    parser.add_argument('--pull-all', action='store_true', help='Pull all VEX from scene')
    parser.add_argument('--pull', metavar='PATH', help='Pull VEX from specific node')
    parser.add_argument('--push', metavar='PATH', help='Push VEX to specific node')
    parser.add_argument('--code', help='VEX code to push (use with --push)')
    parser.add_argument('--watch', action='store_true', help='Watch for changes')
    parser.add_argument('--ingest', action='store_true', help='Ingest pulled VEX to corpus')

    args = parser.parse_args()

    bridge = HoudiniBridge(host=args.host, port=args.port)

    if not bridge.connect():
        sys.exit(1)

    try:
        if args.pull_all:
            nodes = bridge.pull_all_vex()
            for node in nodes:
                print(f"\n{'='*60}")
                print(f"Node: {node.path}")
                print(f"Type: {node.node_type}")
                print(f"Context: {node.context}")
                print(f"{'='*60}")
                print(node.vex_code)

            if args.ingest and nodes:
                bridge.ingest_to_corpus(nodes)

        elif args.pull:
            node = bridge.pull_node(args.pull)
            if node:
                print(f"Node: {node.path}")
                print(f"Context: {node.context}")
                print("-" * 40)
                print(node.vex_code)

                if args.ingest:
                    bridge.ingest_to_corpus([node])
            else:
                print(f"Node not found: {args.pull}")

        elif args.push:
            if not args.code:
                print("Error: --push requires --code")
                sys.exit(1)

            success = bridge.push_vex(args.push, args.code)
            if success:
                print(f"Successfully pushed VEX to {args.push}")
            else:
                print(f"Failed to push VEX to {args.push}")

        elif args.watch:
            print("Watching for VEX changes... (Ctrl+C to stop)")
            asyncio.run(bridge.watch())

        else:
            # Default: just show connection status
            nodes = bridge.pull_all_vex()
            print(f"\nConnected to Houdini at {args.host}:{args.port}")
            print(f"Found {len(nodes)} wrangle nodes in scene")
            for node in nodes:
                print(f"  - {node.path} ({node.context})")

    finally:
        bridge.disconnect()


if __name__ == "__main__":
    main()
