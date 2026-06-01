# Sample Strategy: High-Quality Open-Source VEX for Houdini 21.0.630+

> Status: proposal / strategy. Target runtime: **Houdini 21.0.630 and above**.
> Scope domains: **look development, lighting, APEX, MPM, Solaris, procedural
> modeling, TOPs**.
> Author intent: expand `vex-corpus` beyond its current SOP/math core into the
> modern Houdini feature set, using only material we are legally allowed to
> redistribute, and only material we have *verified to compile/run*.

---

## 1. First principles

Before deciding *what* to add, we fix three definitions, because every later
decision falls out of them.

### 1.1 What is this corpus *for*?

It feeds the RAG/knowledge layer of Synapse and is also a training/eval set.
That means a chunk is valuable only if it is **retrievable** (good prompt +
metadata) and **trustworthy** (the code actually works on the target
version). A plausible-looking snippet that doesn't compile is worse than no
chunk at all, because RAG will surface it with confidence. Therefore:

> **Quality is defined as: compiles/runs on Houdini 21.0.630, is correctly
> attributed to an open-source license, and is correctly classified.**
> Everything else (style, elegance) is secondary.

### 1.2 What counts as a "VEX sample" in these domains?

This is the crux, and it is uncomfortable. The seven requested domains have
**radically different VEX surface area**. Several of them are not primarily
VEX domains at all — they are USD, MaterialX, Python/PDG, or graph-based
(APEX). If we pretend otherwise we will manufacture low-quality or off-topic
chunks. The honest map:

| Domain | Primary language/system in H21 | Genuine VEX surface area | Verdict |
|---|---|---|---|
| **Procedural modeling** | SOPs + wrangles | **Very high** — this is native VEX | Harvest + author aggressively |
| **MPM** (new in H21) | SOP MPM Solver + DOP microsolvers | **Medium-high** — Geometry/Gas Field Wrangles for custom stress, forces, per-particle attrs (`v`, `Je`, `Jp`, targets) | Author-first (little open-source corpus exists yet) |
| **Look development** | MaterialX / Karma / VOPs | **Medium** — CVEX surface/displacement shaders, snippet/inline-code VOPs, ramp/noise driving | Author CVEX; harvest sparingly |
| **Lighting** | USD (Solaris) + Karma | **Low** — mostly USD prims/light params; VEX appears only in CVEX light filters, point-instance attr wrangles, light-linking attr setup | Niche; author the few real VEX touchpoints |
| **Solaris** | USD / LOPs (Python + node graph) | **Low** — LOP-level work is USD/Python; VEX appears upstream (SOP-to-LOP attr prep, `usdimport`/`scenecharacterimport` post-wrangles, instancer attrs) | Capture the SOP↔USD bridge wrangles only |
| **APEX** | APEX graph (separate from VEX) | **Low-medium** — APEX is its *own* graph language; VEX enters only via the `RunVex`/VEX-snippet APEX node (named in/out, **no `@` syntax**) | Treat as a distinct sub-language; tag clearly |
| **TOPs / PDG** | Python + node graph | **Near-zero** — work items are Python/attributes; almost no VEX | Smallest allocation; capture only attr-wrangle-in-PDG cases |

**Strategic consequence #1:** The corpus's identity must stretch from
"VEX snippets" to "**VEX and VEX-adjacent expression code across Houdini
contexts**." We keep VEX central, but we explicitly admit CVEX shaders and
APEX-VEX snippets as first-class, tagged distinctly so retrieval never
confuses an APEX `RunVex` snippet (no `@`) with a SOP wrangle.

**Strategic consequence #2:** For lighting / Solaris / TOPs, we deliberately
keep volume *low and surgical*. We index the real VEX touchpoints (the
SOP-side attribute prep that makes those contexts work) rather than padding
the corpus with USD/Python that isn't VEX. Honesty about surface area is what
keeps the corpus high-signal.

### 1.3 What does "open source" actually bind us to?

"Open source samples" is the hard constraint, and it is mostly a *legal*
problem, not a technical one. We can only ingest **code** we are licensed to
redistribute. Prose can sometimes be summarized; verbatim code cannot be
relicensed away from its origin. See §4 for the full framework. The
one-line version:

> **No license → no code in the corpus.** A snippet with unknown provenance
> is treated as poison, not as a "maybe."

---

## 2. Where the corpus is today (baseline)

- **2,513 chunks, 100% `vex_context: ["sop"]`**, dominated by math (981),
  point-cloud ops (318), channel refs (169). Source era: Houdini 18.0–19.5.
- Schema (`pipeline/schema.py`, `ChunkV2`) already supports per-chunk
  `houdini_version_min`, `functions_referenced`, multi-value `vex_context`,
  and `source_authority`. Enums currently stop at
  `sop/dop/cop/chop/cvex/material/solver`.
- A working extraction bridge exists (`houdini/bridge.py`, hrpyc on
  port 18811) that already understands `attribwrangle`, `volumewrangle`,
  `popwrangle`, `gaswrangle`. This is our verification + authoring lever.

**Gap:** zero coverage of any H20/H21 feature; no MPM, no APEX, no Solaris/USD,
no CVEX shading, no TOPs. Every requested domain is greenfield.

---

## 3. The quality bar (non-negotiable gate)

Every new chunk must pass an automated gate before it can enter
`merged_corpus.jsonl`:

1. **Compiles.** Run the code through the offline VEX compiler shipped with
   Houdini:
   - SOP/DOP/POP/CHOP wrangles, CVEX, shaders → `vcc` (the standalone VEX
     compiler) with the matching context flag. `vcc` validates syntax and
     signatures without a GUI session.
   - APEX `RunVex` snippets → compiled in their named-in/out harness via
     `hython` against an APEX graph (cannot use `vcc`'s `@`-binding path).
   - Things that are not VEX (USD/LOP setups, PDG graphs) → validated by
     **cooking a minimal `.hip`/`.usda` in `hython`**, not by `vcc`.
2. **Runs on a minimal scene.** `hython` cooks a tiny purpose-built scene
   (e.g. a grid + point wrangle, or a small MPM source + solver) and asserts
   no errors and the expected attribute appears. The existing `houdini/bridge.py`
   `push_vex` path is the seed for this — extend it with a headless
   `cook + check` mode.
3. **Version-stamped.** Set `houdini_version_min` from the actual feature
   (e.g. `"21.0"` for MPM SOP solver) and record behavior caveats in
   `houdini_version_notes`. Run the gate on **21.0.630 specifically** so the
   stamp is truthful.
4. **License-stamped.** `source_id` must map to a `sources.yaml` entry whose
   `license` field is set and redistribution-compatible (§4). No license = rejected.
5. **Deduped.** Existing SHA-256 checksum dedup applies; near-dup detection
   (`scripts/quality/dedup_report.py`) extended to catch trivial variants.

> Build the gate **once, first** (Phase 0). It is the single highest-leverage
> investment: it is what lets us trust authored *and* harvested content, and
> it is what makes "Houdini 21.0.630+" a verifiable claim rather than a hope.

---

## 4. Open-source / licensing framework

### 4.1 License tiers

| Tier | Licenses | Can we redistribute code? | Obligation |
|---|---|---|---|
| **Green** | MIT, BSD-2/3, Apache-2.0, Unlicense, CC0, ISC | Yes | Keep notice + attribution; record SPDX id in chunk |
| **Yellow** | CC BY, CC BY-SA | Yes, **with attribution**; BY-SA forces share-alike on derivatives | Attribute; flag the corpus license interaction (see §4.3) |
| **Red** | No license stated, "all rights reserved", proprietary, SideFX docs/HDAs, forum posts, Patreon/Gumroad paywalled | **No** | Reference-only: link + summarize in prose, store **zero verbatim code** |
| **Author** | We write it | Yes — we choose the license | Default to MIT to match repo |

Key clarifications:

- **SideFX official docs and shipped example HDAs are Red.** The repo already
  treats SideFX VEX docs as "reference indexing only" — keep that. We may store
  the function *signature* and a one-line description (facts, not creative
  expression) and link out, but not copy their example code wholesale.
- **A GitHub repo with no LICENSE file is Red,** not green. Absence of a
  license means default copyright. This trips people up constantly.
- **odforce / SideFX forum / Discord snippets are Red** unless the author
  explicitly licensed them. Tempting, high-volume, and legally radioactive —
  do not ingest code from them.

### 4.2 Candidate open-source sources (verify license at ingest time)

Confirmed-existing, license worth confirming per repo before harvest:

| Source | Domains | Likely license | Action |
|---|---|---|---|
| **thi-ng/vexed-generation** | procedural modeling, geometry ops (VEX/OpenCL helper lib) | MIT | Harvest header funcs as `pattern` chunks |
| **toby5001/Houdini-Snippets** | procedural, general VEX/OpenCL | Apache-2.0 | Harvest |
| **jtomori/vex_tutorial** | VEX fundamentals, procedural | Check (MIT-ish) | Harvest if green |
| **Kuchavo/VEX-Snippets** | masks, remaps, geo manipulation | Check | Harvest if green |
| **kiryha/Houdini wiki** | beginner→intermediate VEX | Check wiki license | Already a planned source |
| **tokeru.com cgwiki** (Estela) — incl. `HoudiniApex.html` | APEX, VEX, broad | **CC BY-SA** (Yellow) | Already planned; APEX page is valuable & rare |

> For **MPM and APEX specifically, almost no green-tier open-source corpus
> exists yet** (features are new; most material is paywalled tutorials =
> Red). This is the decisive finding: **for new domains, harvesting is not
> viable, so we must author.** See §5.

### 4.3 Corpus license interaction

The repo is MIT. Ingesting **CC BY-SA** (cgwiki) code into an MIT corpus is a
genuine tension: BY-SA is share-alike/copyleft-ish for that *content*. We do
**not** dissolve BY-SA into MIT. Mitigation:

- Store per-chunk `license` (SPDX) and `attribution` fields (schema change,
  §6). The corpus file then carries mixed licenses transparently.
- Keep a top-level `LICENSES.md` / `ATTRIBUTION.md` enumerating each source's
  license, mirroring how the README already attributes Estela/Arcara.
- Prefer **paraphrasing concepts** + **authoring our own equivalent code**
  over copying BY-SA code verbatim where practical — author code is MIT and
  removes the share-alike entanglement entirely.

---

## 5. Sourcing strategy: harvest vs. author (per domain)

The split is driven entirely by §1.2 (surface area) and §4.2 (what green-tier
material exists):

| Domain | Strategy | Rationale | Target chunks (v1) |
|---|---|---|---|
| Procedural modeling | **Harvest-led**, author to fill gaps | Native VEX + several green repos exist | 250–400 |
| Look development (CVEX) | **Author-led** | Little open CVEX; we control quality | 60–100 |
| MPM | **Author-only** | New in H21; no green corpus; must verify on 21.0.630 | 60–120 |
| APEX (RunVex) | **Author + cgwiki(BY-SA)** | Distinct sub-language; tiny green pool | 30–60 |
| Solaris (SOP↔USD wrangles) | **Author, surgical** | Capture only the real VEX bridge cases | 30–50 |
| Lighting (CVEX filters/instancer attrs) | **Author, surgical** | Low VEX surface | 15–30 |
| TOPs/PDG | **Author, minimal** | Near-zero VEX | 10–20 |

### 5.1 Authoring program (the centerpiece)

Because new domains can't be harvested, the strategy's core engine is a
**verified authoring loop**, not a scraper:

1. **Taxonomy first.** For each domain, enumerate the canonical tasks a TD
   actually performs (e.g. MPM: "drive `targetP` for shape-matching", "add a
   custom vortex force in a Gas Field Wrangle", "color particles by `Je`
   elastic strain"). This task list *is* the spec for authoring.
2. **Author minimal, idiomatic snippets** per task — small, single-concept,
   the way the existing beginner chunks are (`@Cd = @N;` style), scaled up by
   difficulty.
3. **Verify** through the Phase-0 gate on 21.0.630 (compile + cook + assert).
4. **Enrich** through the existing LLM pipeline (prompt, alt-prompts,
   explanation, classification) — but with `vcc`/cook ground truth overriding
   any LLM-guessed `functions_referenced`/`attributes_*`.
5. **License = MIT**, authored in-repo under e.g. `data/authored/<domain>/`.

This makes us the *primary source* for H21 MPM/APEX/Solaris VEX, which is
exactly the gap nobody else has filled — a durable advantage for Synapse.

### 5.2 Harvesting pipeline (procedural + general VEX)

Extend `scripts/scrapers/` with a **license-aware GitHub harvester**:

- Resolve each repo's SPDX license via the GitHub API **before** fetching code;
  abort on Red/absent.
- Parse `.vfl`/`.h`/`.vex` files and fenced ```vex code blocks in `.md`.
- Emit `ChunkV2` with `license`, `attribution`, `source_url` (commit-pinned
  permalink), then run the same quality gate as authored content.

---

## 6. Schema & config changes

Small, additive, backward-compatible (every field keeps a default).

**`pipeline/schema.py`:**

1. Extend `VEXContext` enum (new contexts are real H21 surfaces):
   ```python
   LOP = "lop"        # Solaris / USD-side wrangles
   APEX = "apex"      # APEX RunVex snippets (named in/out, no @ syntax)
   ```
   Keep `material` for CVEX/MaterialX shading; keep `solver` for MPM/DOP
   microsolver wrangles. Add a finer `subcontext` free-string (e.g.
   `"mpm"`, `"karma"`, `"gas_field_wrangle"`, `"pdg_attrib"`).
2. Add fields to `ChunkV2`:
   ```python
   license: str = ""          # SPDX id, e.g. "MIT", "CC-BY-SA-4.0"
   attribution: str = ""      # author/source credit string
   houdini_version_max: str = ""   # if a feature was later removed/changed
   verified: bool = False     # passed the compile+cook gate
   verification_method: str = ""   # "vcc" | "hython-cook" | "apex-harness"
   ```
3. `domain` tag (free string or new enum) so retrieval can filter by the seven
   areas independently of `vex_context`.

**`config/sources.yaml`:** add a required `license:` (SPDX) and `attribution:`
to every source; add new green sources from §4.2 with `status: planned`; add an
`authored-h21-samples` source (`authority: 0.85`, `license: MIT`,
`houdini_version_era: "21.0"`).

**Migration:** existing 2,513 chunks default to `license: ""`,
`verified: false`. Backfill the legacy Joy-of-VEX/cgwiki licenses in one pass;
mark legacy chunks `verified: false` honestly (they were never compiler-checked)
and optionally re-run them through the new gate over time.

---

## 7. Roadmap (phased, each phase independently shippable)

**Phase 0 — Quality gate (foundational, do first).**
Build the `vcc` + `hython` headless verification harness (extends
`houdini/bridge.py`). Add `verified`/`license` schema fields. Deliverable:
`scripts/quality/verify_vex.py` that takes a chunk and returns
pass/fail + extracted ground-truth functions/attributes. *Without this,
nothing else can claim "high quality, Houdini 21.0.630."*

**Phase 1 — Procedural modeling (harvest).**
Stand up the license-aware GitHub harvester; ingest green repos
(vexed-generation, Houdini-Snippets, jtomori, etc.); verify all on 21.0.630.
Highest ROI: native VEX, real open-source supply. ~250–400 chunks.

**Phase 2 — MPM (author).**
Author + verify the MPM task taxonomy (Geometry/Gas Field Wrangle forces,
stress, `targetP`, strain-based color). This is the flagship differentiator.
~60–120 chunks, every one cooked against the real H21 MPM solver.

**Phase 3 — Look development + lighting (author CVEX).**
CVEX surface/displacement patterns, inline-code VOP snippets, Karma-driving
attribute setups; the few real lighting VEX touchpoints. ~75–130 chunks.

**Phase 4 — APEX + Solaris + TOPs (author, surgical).**
APEX `RunVex` snippets (tagged distinctly, no `@`), SOP↔USD bridge wrangles,
the rare PDG attribute-wrangle cases. ~70–130 chunks. Keep volume honest.

**Phase 5 — Backfill & rebalance.**
License-stamp legacy chunks; re-verify a sampled subset; update README topic
table; rerun `sync_to_synapse.py`; add agent routing for the new contexts.

Rough v1 target: **~500–850 new, verified, correctly-licensed chunks** spanning
all seven domains, with volume concentrated where VEX actually lives.

---

## 8. Anti-goals & risks (what we will *not* do)

- **Won't pad lighting/Solaris/TOPs** with USD/Python that isn't VEX just to
  hit a number. Surface area honesty > chunk count.
- **Won't ingest unlicensed/forum/paywalled code.** Red tier is a hard wall.
- **Won't trust LLM-extracted `functions_referenced`/attributes** when the
  compiler can tell us the truth — `vcc`/cook output overrides the LLM.
- **Won't conflate APEX-VEX with SOP-VEX** — different binding model (`@` vs
  named ports); mis-tagging here actively harms retrieval.
- **Risk: feature drift across 21.0.x builds.** Mitigate by stamping the exact
  tested build and re-running the gate when bumping the target.
- **Risk: MPM/APEX APIs still evolving.** Mitigate with `houdini_version_notes`
  and a periodic re-verification job.

---

## 9. Immediate next actions

1. Approve the scope split in §1.2 (the surface-area matrix) — it drives
   everything.
2. Build Phase 0 verification harness + schema fields (§3, §6).
3. Confirm licenses for the §4.2 candidate repos; populate `sources.yaml`.
4. Draft the MPM and procedural task taxonomies (§5.1 step 1) as the authoring
   spec.

---

### Sources consulted

- MPM / custom VEX forces: [SideFX MPM Solver node](https://www.sidefx.com/docs/houdini/nodes/sop/mpmsolver.html), [Gas Field Wrangle](https://www.sidefx.com/docs/houdini/nodes/dop/gasfieldwrangle.html), [Creating Custom Solvers with VEX Wrangles](https://www.sidefx.com/tutorials/creating-custom-solvers-with-vex-wrangles/)
- APEX + VEX: [APEX nodes](https://www.sidefx.com/docs/houdini/nodes/apex/index.html), [APEX Script SOP](https://www.sidefx.com/docs/houdini/nodes/sop/apex--script.html), [cgwiki HoudiniApex](https://www.tokeru.com/cgwiki/HoudiniApex.html), [VEX snippets](https://www.sidefx.com/docs/houdini/vex/snippets.html)
- Open-source VEX repos: [thi-ng/vexed-generation](https://github.com/thi-ng/vexed-generation), [toby5001/Houdini-Snippets](https://github.com/toby5001/Houdini-Snippets), [jtomori/vex_tutorial](https://github.com/jtomori/vex_tutorial), [Kuchavo/VEX-Snippets](https://github.com/Kuchavo/VEX-Snippets), [kiryha/Houdini wiki](https://github.com/kiryha/Houdini/wiki/vex-snippets), [AwesomeHoudini](https://github.com/wyhinton/AwesomeHoudini)
</content>
</invoke>
