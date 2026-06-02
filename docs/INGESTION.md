# Ingesting vex-corpus into Synapse

This corpus is built from several inputs that accumulate in separate files.
For ingestion you do **not** deal with those individually -- you build one
canonical file and sync it.

## The two-step flow

```bash
# 1. Unify everything into the canonical corpus + manifest
python scripts/build_corpus.py

# 2. Sync the canonical corpus into a local Synapse checkout
python scripts/sync_to_synapse.py --synapse /path/to/Synapse
```

`build_corpus.py` is idempotent; `sync_to_synapse.py` skips work if the corpus
is unchanged (use `--force` to override, `--dry-run` to preview).

## What `build_corpus.py` produces

| File | Purpose |
|------|---------|
| `output/corpus/vex_corpus.jsonl` | **The canonical corpus.** One JSONL line per chunk, every chunk normalized. This is the only file you ingest. |
| `output/corpus/corpus_manifest.json` | The ingestion front door: schema version + counts by domain / context / source / license / difficulty / verification. |

### Inputs unified (dedup precedence: first wins)

1. `output/corpus/merged_corpus.jsonl` — legacy enriched chunks (Joy of VEX, cgwiki, SideFX, blueprints)
2. `output/authored/authored_corpus.jsonl` — authored H21 samples (`scripts/import_authored.py`)
3. `output/best_practices/best_practices_corpus.jsonl` — VEX blocks from the H21 best-practices guide (`scripts/ingest_best_practices.py`)
4. `output/harvest/*.jsonl` — license-aware GitHub harvest (`scripts/scrapers/harvest_github.py`)

> Best-practices code from an H21 domain section joins that domain's reference
> file in Synapse; general VEX best practices and anti-patterns group under the
> `best_practices` topic (its own reference file).

### Normalization guarantees (why ingestion is "easy")

- **Every chunk carries `llm_topic`.** The Synapse sync groups by `llm_topic`
  and *drops* chunks that lack it. The builder guarantees the key exists:
  legacy `llm_topic` is preserved; H21 chunks group under their `domain`;
  anything else falls back to `uncategorized`. **No chunk is silently lost.**
- **Every chunk carries `domain`.** Legacy → `fundamentals`; H21 content keeps
  its workflow domain (`mpm`, `apex`, `look_development`, ...).
- **Every chunk carries `license` + `attribution`,** backfilled from
  `config/sources.yaml`.
- **Redistribution review flag.** Chunks whose source is `reference_only`
  (e.g. SideFX docs) but that contain code are marked
  `redistribution_review: true`. These should be resolved before publishing
  the synced output -- see the manifest's `redistribution_review_chunks` count.

## What `sync_to_synapse.py` writes into Synapse

- **`rag/skills/houdini21-reference/vex_corpus_*.md`** — one reference file per
  major topic. H21 domains (`mpm`, `apex`, `look_development`, `lighting`,
  `solaris`, `tops`, `procedural_modeling`) always get their own file via
  `FORCE_TOPIC_KEYS`, even when small, so they stay discoverable.
- **`rag/documentation/_metadata/semantic_index.json`** — one entry per topic
  (keywords, summary, plus `licenses` / `verified_examples` /
  `houdini_version_min` provenance signal). Stale `vex_corpus_*` entries are
  pruned.
- **`rag/documentation/_metadata/agent_relevance_map.json`** — topic → agent
  routing (currently all `sop_agent`).
- **`.vex_corpus_manifest.json`** — incremental-sync manifest in the reference
  dir.

All generated files use the `vex_corpus_` prefix to avoid colliding with
Synapse's curated content.

## Verifying before you publish

```bash
# Preview the sync without touching Synapse
python scripts/sync_to_synapse.py --synapse /path/to/Synapse --dry-run

# Inspect the manifest (counts, license breakdown, review flags)
cat output/corpus/corpus_manifest.json
```
