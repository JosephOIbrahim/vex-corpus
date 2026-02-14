# VEX-CORPUS Pipeline Analysis

Generated: 2026-02-14
Repo: `C:\Users\User\vex-corpus`

---

## PIPELINE MAP

```
                         VEX-CORPUS DATA FLOW
                         =====================

  SOURCES                INGESTION           TIER 1 (Classification)
  -------                ---------           -----------------------
  .vex/.vfl files  --+                       nemotron-mini 4.2B
  .md (fenced)     --+--> VEXParser -------> 6 parallel tasks:
  .json (nested)   --+   (intake.py)  |       1. classify_wrangle (context)
  Houdini hrpyc    --+                |       2. extract_attributes (read/write)
  Watch dirs       --+                |       3. extract_functions (VEX calls)
                                      |       4. detect_bugs (issues)
                      Dedup by        |       5. estimate_complexity (O(n))
                      SHA256[:16]     |       6. classify_topic (bucket)
                                      |
                                      v
                              TIER 2 (Generation)
                              -------------------
                              nemotron-3-nano 31.6B
                              3 tasks:
                                7. generate_prompt (NL instruction)
                                8. generate_explanation (2-3 paragraphs)
                                9. rate_difficulty (1-10)
                              + optional:
                                10. inject_bug
                                11. paraphrase_prompt (3-5 alts)
                                12. infer_context
                                      |
                                      v
                              EXPORT
                              ------
                              -> corpus_*.json    (full snapshot)
                              -> training_*.jsonl (prompt/completion pairs)
                              -> chat_*.jsonl     (messages format)
                              -> metadata_*.csv   (classification summary)
```

### Stage-by-Stage Detail

| # | Stage | File | Input | Output | Model |
|---|-------|------|-------|--------|-------|
| 1 | Parse | `pipeline/intake.py` | .vex/.vfl/.md/.json/Houdini | `VEXSample` (pending) | - |
| 2 | Dedup | `pipeline/intake.py` | VEXSample | pass/reject | SHA256 hash |
| 3 | Classify context | `ollama/src/dispatcher.py` | VEX code | point/prim/vertex/detail | nemotron-mini |
| 4 | Extract attrs | `ollama/src/dispatcher.py` | VEX code | attrs read/written, ch() | nemotron-mini |
| 5 | Extract funcs | `ollama/src/dispatcher.py` | VEX code | function call list | nemotron-mini |
| 6 | Detect bugs | `ollama/src/dispatcher.py` | VEX code | bug list + severity | nemotron-mini (consensus) |
| 7 | Estimate complexity | `ollama/src/dispatcher.py` | VEX code | O(1)/O(n)/O(n^2) | nemotron-mini (consensus) |
| 8 | Classify topic | `ollama/src/dispatcher.py` | VEX code | topic bucket | nemotron-mini (consensus) |
| 9 | Generate prompt | `ollama/src/dispatcher.py` | VEX code + classification | NL instruction | nemotron-3-nano |
| 10 | Generate explanation | `ollama/src/dispatcher.py` | VEX code + classification | 2-3 paragraphs | nemotron-3-nano |
| 11 | Rate difficulty | `ollama/src/dispatcher.py` | VEX code + classification | 1-10 score | nemotron-3-nano (consensus) |
| 12 | Export | `orchestrator.py` | complete VEXSample | JSON/JSONL/CSV | - |

### Execution Modes

| Mode | Entry Point | Description |
|------|-------------|-------------|
| `orchestrator` | `orchestrator.py` | Async subagent architecture, 2 Tier1 + 1 Tier2 workers |
| `daemon` | `autonomous.py` | Self-healing, file watcher, auto-export every 5 min |
| `gui` | `gui/app.py` | PyQt6 dashboard with table view |
| `once` | `launch.py` | Single-run batch mode |
| `ask` | `ask.py` | Natural language query against corpus |

---

## CHUNK SCHEMA (Current)

The `VEXSample` dataclass lives in `pipeline/intake.py:30-117`.

### Fields

```
VEXSample
+-- id: str                         # "vex_{hash16}" or "joy_of_vex_ep01_001"
+-- code: str                       # Raw VEX code
+-- source_file: str                # File path or YouTube URL
+-- source_line: int                # Line number (0 for non-file sources)
+-- hash: str                       # SHA256[:16] for dedup
+-- created_at: str                 # ISO timestamp
+-- stage: ProcessingStage          # pending -> tier1 -> tier2 -> complete
|
+-- classification:
|   +-- context: str                # "point"|"prim"|"vertex"|"detail"|"SOP"
|   +-- context_confidence: float   # 0.0-1.0
|   +-- attributes_read: list       # [{"name": "@P", "type": "int", "builtin": true}]
|   +-- attributes_written: list    # same format
|   +-- channels: list              # ch() parameter references
|   +-- functions: list             # VEX function calls
|   +-- bugs: list                  # detected issues
|   +-- complexity: str             # "O(1)"|"O(n)"|"simple"|"moderate"|"complex"
|   +-- topic: str                  # "color"|"math_operations"|"point_cloud_ops" etc.
|
+-- generation:
|   +-- prompt: str                 # NL instruction ("Write a VEX snippet that...")
|   +-- alternative_prompts: list   # 3-5 paraphrases (sometimes just keywords)
|   +-- explanation: str            # 2-3 paragraph explanation
|   +-- difficulty: int             # 1-10 (Joy of VEX data uses 1-5)
|
+-- quality:
    +-- flagged_for_review: bool    # True if low confidence
    +-- review_reason: str          # Why flagged
```

### Sample Chunk (from pipeline output)

```json
{
  "id": "vex_83659f3d62e3234c",
  "code": "int h = pcopen(0, \"P\", @P, 1.0, 10); pcclose(h);",
  "source_file": "C:\\Users\\User\\vex-corpus\\input\\test2.vex",
  "source_line": 1,
  "hash": "83659f3d62e3234c",
  "created_at": "2026-01-01T20:38:41.380310",
  "stage": "complete",
  "classification": {
    "context": "point",
    "context_confidence": 1.0,
    "attributes_read": [{"name": "@P", "type": "int", "builtin": true}],
    "attributes_written": [],
    "channels": [],
    "functions": [],
    "bugs": [],
    "complexity": "O(1)",
    "topic": ""
  },
  "generation": {
    "prompt": "Write a VEX snippet that counts and stores the number of points...",
    "alternative_prompts": ["How can I count nearby points...", ...],
    "explanation": "This VEX snippet performs a point cloud query...",
    "difficulty": 3
  },
  "quality": {
    "flagged_for_review": false,
    "review_reason": ""
  }
}
```

### Sample Chunk (from Joy of VEX import)

```json
{
  "id": "joy_of_vex_ep01_001",
  "code": "@Cd = @N;",
  "source_file": "https://www.youtube.com/watch?v=9gB1zBa9Lg4&t=776s",
  "source_line": 0,
  "hash": "48d6990b3e52bcf4",
  "stage": "complete",
  "classification": {
    "attributes_read": ["N"],
    "attributes_written": ["Cd"],
    "bugs": [],
    "channels": [],
    "complexity": "simple",
    "context": "SOP",
    "context_confidence": 0.85,
    "functions": [],
    "topic": "color"
  },
  "generation": {
    "alternative_prompts": ["attributes", "normals", "color space", "vector attributes"],
    "difficulty": 1,
    "explanation": "Sets the color attribute (@Cd) equal to the normal vector...",
    "prompt": "Setting Color from Normal"
  },
  "quality": {
    "flagged_for_review": false,
    "review_reason": ""
  }
}
```

### Schema Inconsistencies Found

| Field | Pipeline Output | Joy of VEX Import | Issue |
|-------|----------------|-------------------|-------|
| `classification.context` | `"point"` (lowercase) | `"SOP"` (uppercase) | Inconsistent casing/semantics |
| `classification.complexity` | `"O(1)"`, `"O(n)"` | `"simple"`, `"moderate"`, `"complex"` | Two different scales |
| `classification.attributes_read` | `[{name, type, builtin}]` | `["N", "P"]` (flat strings) | Different formats |
| `generation.alternative_prompts` | Full sentences | Single keywords | Different granularity |
| `generation.difficulty` | 1-10 scale | 1-5 scale (mapped from beginner/intermediate/advanced) | Different ranges |

---

## GAP ANALYSIS

| # | Requirement | Current Status | Priority | Effort |
|---|-------------|----------------|----------|--------|
| 1 | Multiple source ingestion | **Partial** - Only YouTube transcripts + manual .vex files. No web scraping. | HIGH | Medium |
| 2 | VEX code extraction & validation | **Partial** - Parser extracts code from fenced blocks. No syntax validation. | HIGH | Medium |
| 3 | Chunk taxonomy (concept/pattern/reference/troubleshooting) | **Missing** - `topic` field exists but uses ad-hoc labels ("color", "math_operations") | HIGH | Low |
| 4 | Difficulty tagging | **Partial** - Exists but inconsistent (1-10 vs 1-5 vs simple/moderate/complex) | MEDIUM | Low |
| 5 | VEX context tagging (sop/dop/cop/chop/cvex/material) | **Partial** - Only "point/prim/vertex/detail" or "SOP". No DOP/COP/CHOP. | MEDIUM | Low |
| 6 | Function reference linking | **Missing** - `functions` field exists but is almost always empty | HIGH | Medium |
| 7 | Houdini version awareness | **Missing** - No version fields | MEDIUM | Low |
| 8 | Source authority weighting | **Missing** - No authority field | MEDIUM | Low |
| 9 | Deduplication detection | **Partial** - Hash-based exact dedup. No fuzzy/semantic dedup. | MEDIUM | Medium |
| 10 | Prerequisites/dependency graph | **Missing** - No prerequisite linking | LOW | High |
| 11 | Standardized content_type taxonomy | **Missing** - No concept/pattern/reference/troubleshooting labels | HIGH | Low |
| 12 | Unified difficulty scale | **Missing** - Three different scales coexist | MEDIUM | Low |
| 13 | Code-block separation from prose | **Missing** - Code and explanation mixed in `code` field for multi-block chunks | HIGH | Medium |
| 14 | Test suite | **Missing** - `tests/` directory is empty | HIGH | High |

---

## CODE QUALITY ISSUES

### Critical

1. **Hardcoded Windows paths** - `orchestrator.py:481`, `autonomous.py:99,628-669`, `pipeline/intake.py:219`, `demo_job_queue.py:149`
   All use `C:/Users/User/vex-corpus/...` instead of relative paths or config.

2. **Empty test suite** - `tests/` directory exists but contains no test files. Three standalone test scripts (`test_classify.py`, `test_full.py`, `test_integration.py`) exist at repo root but are demo scripts, not pytest tests.

3. **Schema inconsistencies** between pipeline output and Joy of VEX import (see table above). The two data sources produce incompatible schemas in the same corpus.

### High

4. **No structured logging** - All output via `print()` or `rich.console`. No log levels, no rotation.

5. **Missing `functions` extraction** - The `functions` field in Joy of VEX data is always `[]` even for code containing `pcopen`, `pcfind`, `length`, `fit`, etc. The Tier 1 task exists but wasn't run on imported data.

6. **`topic` field is unreliable** - Uses ad-hoc labels inconsistently (`"color"`, `"math_operations"`, `"attributes"`, empty string).

7. **No encoding specification** - `pipeline/intake.py` doesn't specify `encoding='utf-8'` in file reads (Windows default is cp1252).

### Medium

8. **Duplicate content in Joy of VEX corpus** - Many consecutive chunks differ by only 1 line of code appended, suggesting over-granular segmentation from the YouTube transcript.

9. **`alternative_prompts` inconsistency** - Pipeline generates full sentences, Joy of VEX import has single keywords. Not useful for the same purpose.

10. **No `.env` / environment variable support** - API endpoints, model names, and paths all hardcoded.

11. **Magic numbers** - Batch sizes (50), flush intervals (60s), scan intervals (10s), worker counts (2/1) not in config.

### Low

12. **`watch/` and `input/` dirs not in repo** - Referenced by autonomous mode but not created or gitignored.

13. **`nul` file** in repo root - Artifact from Windows null device redirect, should be gitignored.

---

## RECOMMENDED CHUNK SCHEMA (Target)

```python
CHUNK_SCHEMA = {
    # === Content ===
    "id": str,                    # Unique ID: "{source_id}_{sequence}" or "vex_{hash}"
    "content": str,               # Prose content (explanations, context)
    "code_blocks": [              # Extracted VEX code, separated from prose
        {
            "code": str,          # The VEX snippet
            "line_context": str,  # Where in source this appeared
            "is_complete": bool,  # True if syntactically complete
        }
    ],

    # === Classification ===
    "content_type": str,          # concept | pattern | reference | troubleshooting | discussion
    "difficulty": str,            # beginner | intermediate | advanced | expert
    "vex_context": [str],         # [sop, dop, cop, chop, cvex, material, solver]

    # === Source Metadata ===
    "source_id": str,             # Maps to sources.yaml (e.g. "joy-of-vex-youtube")
    "source_url": str,            # Direct URL to source material
    "source_authority": float,    # 0.0-1.0, from sources.yaml
    "title": str,                 # Human-readable chunk title
    "section": str,               # Section within source (e.g. "JoyOfVex Day 7")

    # === VEX-Specific ===
    "functions_referenced": [str],   # ["pcopen", "pcfind", "pcclose"]
    "attributes_read": [str],        # ["P", "N", "Cd"]
    "attributes_written": [str],     # ["pscale", "Cd"]
    "houdini_version_min": str,      # Minimum Houdini version (e.g. "18.0")
    "houdini_version_notes": str,    # Deprecation or behavior change notes
    "prerequisites": [str],          # Chunk IDs this depends on

    # === Pipeline Metadata ===
    "created_at": str,            # ISO timestamp
    "pipeline_version": str,      # Pipeline version that generated this
    "checksum": str,              # SHA256 content hash for dedup detection

    # === Generation (from LLM) ===
    "prompt": str,                # Natural language instruction
    "alternative_prompts": [str], # 3-5 paraphrases (full sentences)
    "explanation": str,           # 2-3 paragraph explanation

    # === Quality ===
    "flagged_for_review": bool,
    "review_reason": str,
    "validation_warnings": [str], # VEX syntax issues found
}
```

### Key Changes from Current Schema

1. **`content` + `code_blocks`** replaces single `code` field - separates prose from code
2. **`content_type`** is new - proper taxonomy instead of ad-hoc `topic`
3. **`difficulty`** standardized to named levels instead of numeric scales
4. **`vex_context`** is a list (code can run in multiple contexts)
5. **`source_id` + `source_authority`** link to source registry
6. **`functions_referenced`** populated by static analysis, not LLM
7. **`houdini_version_min/notes`** new for version awareness
8. **`prerequisites`** new for dependency graph
9. **`checksum`** replaces `hash` (clearer name, full SHA256)
10. **Flat structure** instead of nested classification/generation/quality dicts

---

## CRITICAL PATH

Priority order for implementation:

1. **Schema standardization** (Phase 0.4) - Fix the inconsistency between pipeline and imported data. Everything downstream depends on this.

2. **Source registry** (Phase 0.3) - Create `config/sources.yaml` so new sources have a standard config format.

3. **cgwiki scraper** (Phase 1.1) - The original Joy of VEX TEXT content from tokeru.com is higher quality than YouTube transcript extraction. This is the highest-value new source.

4. **VEX code extractor** (Phase 2.1) - Static analysis for `functions_referenced` fixes the biggest data quality gap (currently always empty).

5. **Auto-tagger** (Phase 2.2) - Rule-based classification for `content_type`, `difficulty`, `vex_context` to fill missing metadata.

6. **SideFX reference scraper** (Phase 1.2) - Function reference chunks enable cross-referencing and are essential for the "what does X do?" query pattern.

7. **Merge script** (Phase 1.4) - Unified corpus from all sources with dedup detection.

8. **Pipeline orchestrator** (Phase 2.5) - Single command to run everything.

9. **Evaluation harness** (Phase 3.3) - Measure retrieval quality before optimizing it.

10. **Search index** (Phase 3.1) - Hybrid BM25 + semantic search for Synapse RAG layer.

---

## CORPUS STATISTICS (Current)

```
Total samples:     1,633 (Joy of VEX import only)
                   + 1 (pipeline-processed test sample)

Source breakdown:
  joy-of-vex-youtube: 1,633 (100%)

Difficulty distribution (Joy of VEX):
  difficulty=1: ~1,400 (85.7%)   # beginner -> 1
  difficulty=3:   ~180 (11.0%)   # intermediate -> 3
  difficulty=5:    ~53  (3.2%)   # advanced -> 5

Topic distribution (Joy of VEX, top 5):
  color:            ~350
  math_operations:  ~280
  attributes:       ~200
  noise:            ~150
  point_cloud_ops:  ~100

Functions field populated: 0 / 1,633 (0%)
Chunks with code: 1,633 / 1,633 (100%)
Avg code length: ~40 characters (very short snippets)
Flagged for review: ~100 (6.1%)
```

### Key Observations

1. **Heavily skewed toward beginner content** - 85% difficulty=1, reflecting Joy of VEX's tutorial nature.
2. **No function extraction** - The `functions` field is always empty despite code containing VEX functions.
3. **Over-granular segmentation** - Many chunks are near-duplicates differing by 1 line, suggesting YouTube timestamp-based chunking captured progressive code building.
4. **Single source** - 100% from YouTube transcripts. No web scrape, no official docs, no pattern library.
5. **Schema drift** - Joy of VEX import uses different formats than the pipeline's native output.
