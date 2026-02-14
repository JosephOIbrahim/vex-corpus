# Pipeline Architecture

## Data Flow

```
  SOURCES                    INGESTION              PROCESSING              EXPORT
  ─────────────────────      ───────────────        ──────────────────      ──────────────
  .vex / .vfl / .h     ─┐
  .md (fenced blocks)   ─┤   VEXParser             Tier 1 (4.2B)           corpus_*.json
  .json (nested code)   ─┼─> (intake.py)  ──────>  6 classification  ──>   training_*.jsonl
  Houdini hrpyc         ─┤   SHA256 dedup          tasks in parallel       chat_*.jsonl
  Watch directories     ─┘                                |                 metadata_*.csv
                                                          v
                                                   Tier 2 (31.6B)
                                                   3 generation tasks
                                                   (sequential per sample)
```

## Orchestrator Subagent Architecture

The `orchestrator.py` runs an async subagent system with work queues:

```
┌──────────────────────────────────────────────────────────────────┐
│                        ORCHESTRATOR                               │
│                                                                   │
│  ┌────────────────┐   ┌────────────────┐   ┌────────────────┐   │
│  │   Ingestion    │   │  Tier1 Worker  │   │  Tier2 Worker  │   │
│  │   Subagent     │   │  Subagent (x2) │   │  Subagent (x1) │   │
│  └───────┬────────┘   └───────┬────────┘   └───────┬────────┘   │
│          │                    │                     │            │
│          v                    v                     v            │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                      WORK QUEUES                          │  │
│  │  [ingest_q] ──> [tier1_q] ──> [tier2_q] ──> [export_q]   │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  STATE: samples dict + seen_hashes set + metrics          │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│  Optional: HoudiniSubagent (hrpyc on port 18811)                │
│  Optional: ExportSubagent (periodic flush)                      │
└──────────────────────────────────────────────────────────────────┘
```

## Task Routing

```
TaskDispatcher.dispatch(task_type, input_data)
  │
  ├─ Tier detection (TIER1_TASKS vs TIER2_TASKS)
  │
  ├─ Consensus check (enabled for: detect_bugs, estimate_complexity,
  │                    classify_topic, rate_difficulty)
  │    YES: Run 3 models in parallel, vote on results
  │    NO:  Single model call
  │
  ├─ Model selection
  │    Tier 1 primary: nemotron-mini (4.2B, ~1s/task)
  │    Tier 2 primary: nemotron-3-nano (31.6B, ~15s/task)
  │    Fallback chain defined in config/models.yaml
  │
  ├─ Ollama request (with timeout + retry from config)
  │
  └─ Quality gate
       confidence < 0.6  -> flag for review
       consensus < 0.66  -> flag for review
       status == ERROR   -> flag for review
```

## Execution Modes

| Mode | Entry Point | Workers | Persistence | Use Case |
|------|-------------|---------|-------------|----------|
| **Orchestrator** | `orchestrator.py` | 2 T1 + 1 T2 | Export on shutdown | Batch processing |
| **Daemon** | `autonomous.py` | 1 T1 + 1 T2 | Auto-export every 5 min | Continuous ingest |
| **GUI** | `gui/app.py` | 1 T1 + 1 T2 | Manual export | Interactive review |
| **Once** | `launch.py --mode once` | 1 T1 + 1 T2 | Export on complete | Single batch |
| **Ask** | `ask.py` | - | Read-only | Corpus query |

## Concurrency Control

- `asyncio.Semaphore(2)` limits concurrent Ollama requests (prevents GPU timeouts)
- Tier 1 tasks run in parallel per sample (6 tasks, semaphore-limited)
- Tier 2 tasks run sequentially per sample (prevents memory pressure on 31.6B model)
- Ollama `keep_alive: 24h` keeps models in VRAM between requests

## File Watcher (Daemon Mode)

```
watch/ and input/ directories
  │
  ├─ Scan every 10 seconds
  ├─ Accumulate batch (5 samples or 5s timeout)
  ├─ Process through Tier 1 + Tier 2
  └─ Auto-export every 5 minutes
```

## Houdini Integration

```
Houdini (port 18811)
  │
  ├─ hrpyc connection via rpyc
  ├─ Extract VEX from attribwrangle / volumewrangle / popwrangle nodes
  ├─ Read 'snippet' parameter
  └─ Optional continuous watch (2s interval)
```
