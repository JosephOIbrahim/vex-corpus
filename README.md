# vex-corpus

A curated dataset of **2,513 labeled VEX code examples** for training, RAG retrieval, and AI-assisted Houdini workflows. Each chunk includes working code, difficulty ratings, topic classification, and rich metadata extracted via a multi-stage LLM enrichment pipeline.

Built to feed the knowledge layer of [**Synapse**](https://github.com/JosephOIbrahim/Synapse) -- an AI exoskeleton for SideFX Houdini.

## Dataset at a Glance

| Metric | Value |
|--------|-------|
| Total chunks | 2,513 |
| LLM-enriched | 2,079 (83%) |
| Chunks with code | 2,083 |
| Unique topics | 28 |
| Corpus size | 5.3 MB (JSONL) |
| Format | Newline-delimited JSON |

### Sources

| Source | Chunks | Description |
|--------|--------|-------------|
| [Joy of VEX](https://www.youtube.com/playlist?list=PLTXmnikJEYnBtSfn4LwKx5vpopwrInp18) | 1,633 | Matt Estela's tutorial series (YouTube recordings by Peter Arcara / SideFX) |
| [SideFX VEX Reference](https://www.sidefx.com/docs/houdini/vex/) | 605 | Official VEX function documentation |
| [cgwiki VEX](https://tokeru.com/cgwiki) | 191 | Matt Estela's written VEX guides |
| VEX Blueprints | 84 | Curated production patterns |

### Topic Distribution

| Topic | Chunks | | Topic | Chunks |
|-------|-------:|-|-------|-------:|
| Math Operations | 981 | | Noise Patterns | 41 |
| Point Cloud Ops | 318 | | Matrix Transforms | 17 |
| Channel References | 169 | | Field Analysis | 15 |
| Geometry Creation | 161 | | Loop Patterns | 12 |
| Edge & Topology | 88 | | String Operations | 6 |
| Color Operations | 79 | | Other (9 topics) | 17 |
| Flow & Visualization | 61 | | |
| Attribute Operations | 55 | | |
| Conditional Logic | 52 | | |

### Difficulty Breakdown

| Level | Chunks |
|-------|-------:|
| Beginner | 257 |
| Intermediate | 1,989 |
| Advanced | 251 |
| Expert | 16 |

## Chunk Schema

Each JSONL line contains a self-describing chunk with 33 fields:

```json
{
  "id": "joy_of_vex_ep01_001",
  "title": "Setting Color from Normal",
  "llm_topic": "color_operations",
  "difficulty": "beginner",
  "content": "Sets the color attribute (@Cd) equal to the normal vector...",
  "code_blocks": [{"code": "@Cd = @N;", "is_complete": true}],
  "functions_referenced": [],
  "attributes_read": ["N"],
  "attributes_written": ["Cd"],
  "vex_context": ["sop"],
  "alternative_prompts": ["attributes", "normals", "color space"],
  "source_id": "joy-of-vex-youtube",
  "source_url": "https://www.youtube.com/watch?v=...",
  "source_authority": 0.9,
  "llm_complexity": "O(1)",
  "llm_code_quality": "clean",
  "llm_bugs": [],
  "llm_bottlenecks": [],
  "checksum": "62413ae2..."
}
```

See `docs/CHUNK_SCHEMA.md` for the complete field reference.

## Synapse Integration

The `sync_to_synapse.py` script transforms the corpus into Synapse's RAG format:

```bash
# Preview what will be generated
python scripts/sync_to_synapse.py --dry-run

# Sync to Synapse (auto-detects sibling Synapse/ directory)
python scripts/sync_to_synapse.py

# Force re-sync even if corpus hasn't changed
python scripts/sync_to_synapse.py --force

# Point to a custom Synapse location
python scripts/sync_to_synapse.py --synapse /path/to/Synapse
```

This generates:
- **14 markdown reference files** in `Synapse/rag/skills/houdini21-reference/` (one per major topic + misc)
- **Semantic index entries** merged into `semantic_index.json` (keyword search)
- **Agent routing entries** merged into `agent_relevance_map.json` (all VEX topics route to `sop_agent`)
- **Manifest file** for incremental sync (skips if corpus unchanged)

All generated files use a `vex_corpus_` prefix to avoid collisions with Synapse's curated content.

## Pipeline Architecture

The corpus is built through a multi-stage enrichment pipeline using local LLMs via [Ollama](https://ollama.com/):

```
Sources --> Ingestion --> Tier 1 Classification --> Tier 2 Generation --> Merge --> Export
                |           (nemotron-mini)          (nemotron-3-nano)
                |           - VEX context            - prompt generation
                |           - attribute extraction   - explanation
                |           - function detection     - difficulty rating
                |           - bug detection
                |           - complexity analysis
                |           - topic classification
                v
            Dedup (SHA256)
```

**Tier 1** (nemotron-mini 4.2B): Fast classification -- context, attributes, functions, bugs, complexity, topic.

**Tier 2** (nemotron-3-nano 31.6B): Deep generation -- natural language prompts, explanations, difficulty ratings.

## Getting Started

### Prerequisites

- Python 3.11+
- [Ollama](https://ollama.com/) running locally (only needed for re-enrichment)

### Install

```bash
git clone https://github.com/JosephOIbrahim/vex-corpus.git
cd vex-corpus
pip install -e ".[dev]"
```

### Use the Corpus Directly

The pre-built corpus is at `output/corpus/merged_corpus.jsonl`. No pipeline run needed:

```python
import json

with open("output/corpus/merged_corpus.jsonl", encoding="utf-8") as f:
    chunks = [json.loads(line) for line in f if line.strip()]

# Find all pcopen examples
pcopen_chunks = [c for c in chunks if "pcopen" in c.get("functions_referenced", [])]
print(f"Found {len(pcopen_chunks)} pcopen examples")
```

### Re-run the Pipeline

Only needed if you're adding new sources or re-enriching:

```bash
# Pull required Ollama models
ollama pull nemotron-mini
ollama pull nemotron-3-nano

# Run the pipeline
python scripts/run_pipeline.py
```

### Run Tests

```bash
python -m pytest tests/ -v
```

## Project Structure

```
vex-corpus/
├── config/
│   ├── models.yaml              # Ollama model routing and task config
│   └── sources.yaml             # Source registry with authority weights
├── data/
│   └── joy_of_vex_corpus.jsonl  # 1,633 imported Joy of VEX samples
├── output/
│   └── corpus/
│       └── merged_corpus.jsonl  # The enriched corpus (2,513 chunks)
├── scripts/
│   ├── sync_to_synapse.py       # Export to Synapse RAG format
│   ├── merge_sources.py         # Merge multiple source JSONL files
│   ├── import_joy_of_vex.py     # Import from Joy of VEX data
│   ├── run_pipeline.py          # Run the enrichment pipeline
│   └── enrichment/              # LLM enrichment modules
├── pipeline/
│   ├── intake.py                # VEXParser, IntakePipeline
│   ├── watcher.py               # File system watcher
│   └── schema.py                # Chunk schema definitions
├── ollama/src/
│   ├── client.py                # Async Ollama client
│   ├── dispatcher.py            # Task routing and consensus
│   └── prompts.py               # LLM task prompts
├── tests/
│   └── test_sync_to_synapse.py  # 52 tests for sync pipeline
└── docs/
    ├── CHUNK_SCHEMA.md          # Full field reference
    ├── ARCHITECTURE.md          # Data flow diagrams
    └── SOURCES.md               # Source documentation
```

## Attribution

- **Joy of VEX** content by Matt Estela ([tokeru.com/cgwiki](https://tokeru.com/cgwiki)), licensed under CC BY-SA
- YouTube recordings by Peter Arcara, published on the [SideFX Houdini channel](https://www.youtube.com/playlist?list=PLTXmnikJEYnBtSfn4LwKx5vpopwrInp18)
- SideFX VEX documentation is (c) SideFX, used for reference indexing only
- This pipeline and its tooling are original work

## License

MIT
