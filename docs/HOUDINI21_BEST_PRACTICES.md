# Houdini 21 Best Practices

A practical, opinionated reference for working effectively in **Houdini 21**
(targeting 21.0.x). Focused on VEX and the modern context-specific workflows,
since this corpus exists to teach those. Where a practice is version-sensitive,
it says so.

> Houdini 21 (released 2025) is a large ".0" with 300+ changes. The headline
> shifts that affect day-to-day work: APEX rigging/animation moved from beta to
> **production**, the **MPM** multi-material solver (introduced in 20.5) was
> extended, **Copernicus** (the rebuilt GPU COPs) is production-ready for most
> uses, **Karma** (CPU + XPU) is the production renderer, and the viewport is
> now **Vulkan** (OpenGL fully retired). Plan new work around these, not the
> legacy equivalents.

---

## 1. General principles

- **Stay procedural and non-destructive.** Prefer parameters, attributes and
  graph structure over baked results. If you hand-edit, do it in a way a node
  can reproduce.
- **Build for change.** Drive values with `ch()`/attributes, not magic numbers.
  Wrap reusable networks in **HDAs** with a clear parameter interface.
- **Name things.** Name nodes for intent (`scatter_debris`, not `scatter3`).
  Name `@name`/`@path` attributes deliberately — they become USD prim paths.
- **Version up, don't overwrite.** Cache and publish to versioned paths
  (`v001`, `v002`). Keep `$HIPNAME`/`$OS`/`$F` in output paths.
- **Profile before optimizing.** Use the **Performance Monitor** (cook times
  per node) and, for GPU work, the **GPU Status** pane. Optimize the node that
  actually dominates, not the one you assume does.
- **Use the built-in node if one exists.** Don't reimplement Fuse, Measure,
  Connectivity, PolyExtrude, etc. in VEX. Hand-write VEX for the logic that
  *doesn't* have a node.

---

## 2. VEX coding best practices

VEX is the backbone of procedural work. Most of these apply in any wrangle.

### Attributes and types
- **Always type your attribute binds.** Write `v@Cd`, `f@pscale`, `i@id`,
  `s@name`, `p@orient`, `4@xform`, `2@uv` (or `u@uv`). An untyped `@foo` defaults
  to float and silently corrupts vector/quaternion data. Type-prefix on first
  use; thereafter `@foo` is fine.
- **Create only the attributes you need.** Every written attribute costs memory
  and travels downstream. Delete scratch attributes when done, or compute in a
  local variable instead of an attribute.
- **Prefer reading existing attributes over recomputing.** If a Normal SOP
  already made `@N`, read it; don't recompute per point.

### Channels and constants
- **Read `ch()` into a local variable** when you use it more than once, both for
  readability and to make intent explicit:
  ```vex
  float amp = chf("amp");
  float freq = chf("freq");
  ```
- Use `chramp()` for art-directable curves instead of hand-rolled remaps.

### Performance inside wrangles
- **A wrangle runs once per element in parallel.** Never assume execution order
  and never rely on another element's *concurrent* writes.
- **Avoid `point()`/`prim()` lookups inside per-element loops.** Random
  attribute fetches are cache-unfriendly. For neighbour queries use
  `neighbours()`/`pointprims()`; for spatial queries use a **point cloud**
  (`pcopen`/`pcfind`/`pcfilter`) or `nearpoints()` — and always `pcclose()` the
  handle.
- **Hoist invariant work out of loops.** Compute constants once before the loop.
- **Initialize array/string detail attributes to the right length** before
  writing into them by index, or use `append()`.
- **Decorrelate random streams** with offset seeds: `rand(@ptnum)`,
  `rand(@ptnum + 1.7)`, … Identical seeds give correlated results.

### Structure and reuse
- **Factor shared logic into VEX functions** and `#include` headers
  (`$HOUDINI_PATH/vex/include`) so wrangles stay small and DRY.
- Keep wrangles **single-concept**. A 200-line wrangle is usually several nodes.
- Comment the *why*, not the obvious.

---

## 3. VEX vs OpenCL vs Compiled Blocks

There is no "always faster" answer — choose by data shape:

| Use | When |
|-----|------|
| **VEX (wrangle)** | The default. CPU, hugely flexible, full geometry API, easy to read. Right for nearly all SOP attribute work. |
| **OpenCL (SOP / DOP microsolver)** | Heavy, *uniform*, arithmetic-dominated work over very large element counts or volumes — especially in simulation inner loops. GPU parallelism wins when the data is large and the kernel is simple. Not for file I/O, recursion, or sparse random access. |
| **Compiled Blocks** | When you need a VEX/SOP loop (For-Each) to run multithreaded and allocation-free over many pieces. Compile-friendly nodes only. |

OpenCL tips: copy `@bindings` into local variables (each bind can re-fetch),
keep kernels branch-light, and watch host↔device transfer — moving data to the
GPU every frame can erase the compute win. Profile with the **GPU Status** pane.

> Rule of thumb: write it in VEX first. Port the *proven* bottleneck to OpenCL
> only after the Performance Monitor says that node dominates.

---

## 4. Context-specific best practices

### 4.1 Procedural modeling (SOP)
- Establish topology early; carry **stable ids** (`@name`, `@id`, connectivity
  `@class`) so downstream nodes and USD stay consistent across cooks.
- Use **groups** to scope operations; prefer attribute-driven group creation.
- For instancing, author `p@orient` (quaternion via `dihedral()`/`quaternion()`),
  `@pscale`, and a prototype index on points, then Copy to Points / Instancer.
- Keep heavy geometry **packed** until you must unpack; it slashes memory and
  speeds up copies and the viewport.

### 4.2 Simulation, solvers and MPM (H21)
- **MPM** (multi-material solver, extended in 21 with debris emission and
  surface tension): set initial state on the **source** (e.g. `v@v`, per-point
  material/pscale) before the solver; apply custom forces in a **Geometry/Gas
  Field Wrangle** stepping the sim, and **scale impulses by `f@TimeInc`** so
  behaviour is substep-independent. Visualize internal state by colouring on
  speed or elastic strain.
- Prefer **deterministic** setups (fixed seeds, fixed substeps) so caches are
  reproducible.
- Cache sims to disk (`.bgeo.sc`/USD) before lighting; never re-sim downstream.
- Use the right solver: **Vellum** for cloth/soft constraints, **MPM** for
  granular/sand/snow/multi-material continua, **Otis** (new in 21) for
  GPU-accelerated muscle/tissue, Pyro (now with a sparse GPU path via
  Copernicus) for volumes.

### 4.3 Look development — materials (CVEX / MaterialX / Karma)
- **Author materials in MaterialX** for portability and Karma (CPU **and** XPU)
  compatibility; use the MaterialX subnet/`mtlx` VOPs rather than legacy
  Principled-only setups when targeting USD/Solaris.
- Drop to **CVEX / Snippet VOPs** for procedural logic a node graph makes
  clumsy (custom masks, fresnel terms, signed displacement). Keep shader VEX
  branch-light — it runs per shading sample.
- **Displacement**: offset `P` along `N`, then ensure normals are recomputed
  (Compute Normal VOP or the displacement node's option) or lighting won't
  follow the surface.
- Build procedural **textures in Copernicus (COPs)** — it's GPU and
  production-ready in 21 for most uses — instead of baking in a DCC round-trip.

### 4.4 Lighting (Solaris / USD / Karma)
- Light in **Solaris (LOPs)** with USD lights; keep lighting in its own layer so
  it composes over layout/FX without editing them.
- Use **light linking** and **light filters** rather than duplicating lights.
- Render with **Karma XPU** for interactive look-dev speed; validate final
  frames on the renderer/precision you'll ship. Use **render purposes**
  (`proxy`/`render`) so viewports stay fast.
- Keep per-light variation data-driven (instance attributes: intensity, colour,
  orient) rather than hand-placing many near-identical lights.

### 4.5 Solaris / USD
- **Think in layers and opinions.** Each department edits its own layer; use
  sublayers/references and let stronger opinions override — never destructively
  flatten shared stages.
- Keep **prim paths stable** (derive `@path`/`@name` from persistent ids, not
  primitive number) so references and overrides survive recooks.
- Use **instancing** (point instancers / scenegraph instances) for large
  populations; tag **purposes** and **kinds** correctly.
- Prefer LOP nodes and Python LOPs for stage edits; reserve SOP VEX for the
  geometry/attribute prep that *feeds* USD import.

### 4.6 APEX (rigging & animation, production in H21)
- Build the skeleton in **KineFX (SOPs)**, then build a procedural **APEX rig**
  on top with rig components — this is the intended H21 workflow, now aimed at
  animators (drag-and-drop viewport rig builder), not only TDs.
- APEX is a **separate graph language** from VEX. When you use a **RunVex**
  node, remember it has **no `@` attribute syntax** — it operates on **named
  inputs/outputs** only. Don't paste SOP wrangle code into it.
- Keep APEX graphs modular; APEX favours many small fast operations over large
  monolithic ones.

### 4.7 TOPs / PDG
- PDG orchestration is **Python-first** (work items, attributes, scheduling).
  VEX appears only in the SOP cooks a work item triggers.
- Drive variation with **work-item attributes / wedges** read on the geometry
  side (e.g. a detail attribute), and stamp the work-item index onto outputs so
  a later merge/ROP can identify them.
- Make tasks **deterministic and idempotent** so dirty/recook and distributed
  scheduling stay correct.

### 4.8 Copernicus (COPs)
- Use Copernicus for **GPU image processing and procedural texturing**; it's
  production-ready in 21 for everything except advanced compositing.
- Keep node networks GPU-friendly (avoid forcing CPU round-trips); leverage the
  sparse GPU solvers and interactive handles it introduces.

---

## 5. Scene structure & pipeline hygiene

- **One responsibility per HDA.** Version HDA definitions; lock published ones.
- **Cache deliberately.** File Cache SOPs to versioned `.bgeo.sc`/USD; don't
  recompute heavy upstream every frame.
- **Keep contexts separate**: model/FX in SOPs → assemble/light in Solaris →
  render in Karma. Don't blur the boundary.
- **Reproducibility**: pin seeds, substeps and ranges; avoid `$T`/wallclock in
  anything cached.
- **Source control friendliness**: prefer HDAs and small `.hip` deltas;
  externalize large caches; commit `.hip`/USD, not gigabytes of geometry.

---

## 6. Migration notes for Houdini 21

- **Viewport is Vulkan-only** — OpenGL is gone. Update GPU drivers; verify any
  custom viewport tooling.
- **APEX rigging is production** — new rigs should target the APEX workflow
  rather than legacy auto-rig / CHOP-heavy setups.
- **MPM extended** — revisit granular/multi-material setups that previously used
  grains/FLIP where MPM now fits better (debris, surface tension).
- **Copernicus replaces the old COPs** for texturing/image work.
- **Karma** is the default path forward over Mantra; author materials in
  MaterialX for CPU/XPU parity.

---

## Sources

- [SideFX: What's new in Houdini 21 — VEX & OpenCL](https://www.sidefx.com/docs/houdini/news/21/vex.html)
- [SideFX: OpenCL for VEX users](https://www.sidefx.com/docs/houdini/vex/ocl.html)
- [SideFX: MPM Solver](https://www.sidefx.com/docs/houdini/nodes/sop/mpmsolver.html), [Gas Field Wrangle](https://www.sidefx.com/docs/houdini/nodes/dop/gasfieldwrangle.html)
- [SideFX: APEX nodes](https://www.sidefx.com/docs/houdini/nodes/apex/index.html), [VEX snippets](https://www.sidefx.com/docs/houdini/vex/snippets.html)
- [CG Channel: Houdini 21 key features](https://www.cgchannel.com/2025/08/sidefx-just-released-houdini-21-check-out-its-5-key-features/)
- [Digital Production: Houdini 21 — Otto, Otis, Copernicus and 300+ features](https://digitalproduction.com/2025/08/05/houdini-21-otto-otis-copernicus-and-300-new-features-no-really/)
