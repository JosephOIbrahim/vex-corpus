# vex-corpus

A multi-stage pipeline that converts VEX learning resources into structured RAG knowledge for **Synapse** -- a Houdini VEX AI assistant built on MCP (Model Context Protocol). Synapse helps artists write, debug, and understand VEX code inside Houdini. This pipeline feeds its knowledge layer.

## Source Material

The primary corpus comes from **Joy of VEX** by Matt Estela ([tokeru.com/cgwiki](https://tokeru.com/cgwiki)), a comprehensive VEX tutorial series. The YouTube recordings by Peter Arcara (SideFX) are indexed as the initial data source. Additional sources (cgwiki text, SideFX official docs, community resources) are planned.

All source material is attributed and authority-weighted in `config/sources.yaml`.

## Pipeline Overview

The pipeline has two tiers of processing, both running locally via [Ollama](https://ollama.com/):

```
Sources -> Ingestion -> Tier 1 Classification -> Tier 2 Generation -> Export
             |              (nemotron-mini)        (nemotron-3-nano)
             |              6 tasks:               3 tasks:
             |              - context              - prompt generation
             |              - attributes           - explanation
             |              - functions            - difficulty rating
             |              - bugs
             |              - complexity
             |              - topic
             v
         Dedup (SHA256)
```

**Tier 1** (nemotron-mini 4.2B, ~119 tok/s): Fast classification -- determines VEX context, extracts attributes/functions, detects bugs, estimates complexity, assigns topic.

**Tier 2** (nemotron-3-nano 31.6B, ~27 tok/s): Deep generation -- writes natural language prompts, multi-paragraph explanations, and difficulty ratings.

## Prerequisites

- Python 3.11+
- [Ollama](https://ollama.com/) running locally
- Required models pulled:
  ```bash
  ollama pull nemotron-mini
  ollama pull nemotron-3-nano
  ```
- Optional: Houdini with hrpyc for live VEX extraction

## Setup

```bash
# Clone and install
cd vex-corpus
pip install -e ".[dev]"

# Verify Ollama is running
curl http://localhost:11434/api/tags
```

## Running the Pipeline

### Unified launcher (recommended)

```bash
python launch.py                    # Interactive mode selection
python launch.py --mode orchestrator  # Async subagent architecture
python launch.py --mode daemon       # Self-healing file watcher
python launch.py --mode once         # Single batch run
python launch.py --mode status       # Show pipeline status
```

### Direct entry points

```bash
python orchestrator.py              # Full async pipeline
python autonomous.py                # Self-healing daemon
python run_gui.py                   # PyQt6 GUI
python ask.py "How do I use pcopen?"  # Query the corpus
```

### Import existing data

```bash
python scripts/import_joy_of_vex.py --input path/to/joy_of_vex_rag.jsonl
```

## Output Format

The pipeline exports in multiple formats:

| Format | File Pattern | Use Case |
|--------|-------------|----------|
| JSON corpus | `corpus_*.json` | Full snapshot with all metadata |
| JSONL training | `training_*.jsonl` | OpenAI fine-tuning (prompt/completion) |
| JSONL chat | `chat_*.jsonl` | Chat format (messages array) |
| CSV metadata | `metadata_*.csv` | Quick analysis in spreadsheets |

Each chunk in the corpus contains classification metadata, generated prompts/explanations, and quality flags. See `docs/CHUNK_SCHEMA.md` for the full schema.

## Project Structure

```
vex-corpus/
├── config/
│   ├── models.yaml          # Ollama model routing and task config
│   └── sources.yaml         # Source registry with authority weights
├── data/
│   └── joy_of_vex_corpus.jsonl  # 1,633 imported Joy of VEX samples
├── docs/
│   ├── ANALYSIS.md          # Pipeline gap analysis
│   ├── ARCHITECTURE.md      # Data flow diagrams
│   ├── CHUNK_SCHEMA.md      # Current and target chunk schema
│   └── SOURCES.md           # Source registry documentation
├── pipeline/
│   ├── intake.py            # VEXParser, VEXSample, IntakePipeline
│   ├── watcher.py           # File system watcher
│   └── schema.py            # Canonical chunk schema definitions
├── ollama/src/
│   ├── client.py            # Async Ollama client
│   ├── dispatcher.py        # Task routing and consensus
│   ├── models.py            # Pydantic task models
│   ├── prompts.py           # 12 focused task prompts
│   └── queue.py             # Job queue with batch processing
├── houdini/
│   ├── bridge.py            # hrpyc connection to Houdini
│   └── enable_port.py       # Start hrpyc server in Houdini
├── gui/
│   └── app.py               # PyQt6 dashboard
├── scripts/
│   └── import_joy_of_vex.py # Import from vex_rag_pipeline
├── orchestrator.py          # Async subagent orchestrator
├── autonomous.py            # Self-healing daemon
├── vex.py                   # Master controller with live dashboard
├── launch.py                # Unified launcher with env validation
├── ask.py                   # Natural language corpus query
└── output/                  # Generated corpus files
```

## Configuration

- **Model routing**: `config/models.yaml` -- tier assignments, timeouts, consensus flags
- **Source registry**: `config/sources.yaml` -- source metadata, authority weights, status

## Attribution

- **Joy of VEX** content by Matt Estela ([tokeru.com/cgwiki](https://tokeru.com/cgwiki)), licensed under CC BY-SA
- YouTube recordings by Peter Arcara, published on the [SideFX Houdini channel](https://www.youtube.com/playlist?list=PLTXmnikJEYnBtSfn4LwKx5vpopwrInp18)
- SideFX VEX documentation is (c) SideFX, used for reference indexing only
- This pipeline and its tooling are original work

## Future Sources

Planned additions (tracked in `config/sources.yaml`):
- tokeru.com cgwiki VEX pages (text versions of Joy of VEX + HoudiniVex tips)
- SideFX official VEX function reference
- VEX for Artists (kiryha/Houdini wiki)
- Production VEX pattern library
