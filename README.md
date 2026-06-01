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

## Houdini 21.0.630+ Expansion

The corpus is being extended beyond its SOP/math core into the modern Houdini
feature set: **procedural modeling, MPM, look development, lighting, APEX,
Solaris, and TOPs**. The full reasoning is in
[`docs/SAMPLE_STRATEGY.md`](docs/SAMPLE_STRATEGY.md). Key principles:

- **Verified, not just plausible.** Every new chunk should pass the quality
  gate (`scripts/quality/verify_vex.py`) -- a `hython` cook on the target build
  when Houdini is present, with a portable static linter as a fallback. Only a
  real cook sets `verified: true` and stamps `verified_houdini_build`.
- **Open-source only.** Code is stored verbatim only under a redistributable
  license (`MIT`, `Apache-2.0`, `CC-BY-SA-4.0`, ...). Unlicensed / forum /
  paywalled material is reference-only.
- **Harvest where supply exists, author where it doesn't.** New H21 features
  (MPM, APEX) have no open corpus yet, so we author MIT-licensed samples.

### Authoring workflow

```bash
# 1. Lint + build the authored sample JSONL from the catalog
python scripts/authoring/build_authored.py

# 2. Ingest -> ChunkV2 (runs the quality gate, stamps license/verification)
python scripts/import_authored.py            # static-lint without Houdini
python scripts/import_authored.py --merge    # also append to merged_corpus

# 3. (On a Houdini 21.0.630 box) verify a JSONL through the cook gate
python scripts/quality/verify_vex.py data/authored/mpm.jsonl
```

Authored samples live in [`scripts/authoring/catalog.py`](scripts/authoring/catalog.py)
(readable, reviewable VEX) and build to `data/authored/*.jsonl`.

### Harvesting open-source repos (Phase 1)

For domains where redistributable open-source VEX already exists (procedural
modeling), `scripts/scrapers/harvest_github.py` ingests a **local checkout** of
a repo. It refuses any source whose `config/sources.yaml` license is not
redistributable (the license wall), extracts per-function chunks from `.h`
headers, whole programs from `.vfl`/`.vex`, and fenced ```` ```vex ```` blocks
from `.md`, and stamps each chunk with a commit-pinned permalink:

```bash
git clone https://github.com/thi-ng/vexed-generation /tmp/vgen
python scripts/scrapers/harvest_github.py --repo-dir /tmp/vgen \
    --source-id thi-ng-vexed-generation \
    --commit $(git -C /tmp/vgen rev-parse HEAD) --domain procedural_modeling
```

## Synapse Integration

Ingestion is a two-step flow (full guide: [`docs/INGESTION.md`](docs/INGESTION.md)):

```bash
# 1. Unify all inputs (legacy + authored + harvested) into one canonical file
python scripts/build_corpus.py
#    -> output/corpus/vex_corpus.jsonl  (the file you ingest)
#    -> output/corpus/corpus_manifest.json  (counts by domain/license/etc.)

# 2. Sync the canonical corpus into a local Synapse checkout
python scripts/sync_to_synapse.py --synapse /path/to/Synapse
```

`build_corpus.py` normalizes every chunk so it always carries `llm_topic`,
`domain`, and `license` -- which means **no chunk is silently dropped** during
sync, and the H21 domains (MPM, APEX, look dev, ...) each get their own
reference file in Synapse.

The `sync_to_synapse.py` script transforms the corpus into Synapse's RAG format:

```bash
# Preview what would be generated (prefers vex_corpus.jsonl, falls back to merged)
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
