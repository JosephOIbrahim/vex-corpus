# Houdini 21 Best Practices

A practical, opinionated, **example-driven** reference for working effectively
in **Houdini 21** (targeting 21.0.x). The focus is VEX and the modern
context-specific workflows, since this corpus exists to teach those. Where a
practice is version-sensitive, it says so.

> Houdini 21 (released 2025) is a large ".0" with 300+ changes. The headline
> shifts that affect day-to-day work: APEX rigging/animation moved from beta to
> **production**, the **MPM** multi-material solver (introduced in 20.5) was
> extended, **Copernicus** (the rebuilt GPU COPs) is production-ready for most
> uses, **Karma** (CPU + XPU) is the production renderer, and the viewport is
> now **Vulkan** (OpenGL fully retired). Plan new work around these, not the
> legacy equivalents.

## Contents

1. [General principles](#1-general-principles)
2. [VEX essentials (quick reference)](#2-vex-essentials-quick-reference)
3. [VEX best practices](#3-vex-best-practices)
4. [Common anti-patterns (before → after)](#4-common-anti-patterns-before--after)
5. [VEX vs OpenCL vs Compiled Blocks](#5-vex-vs-opencl-vs-compiled-blocks)
6. [Context-specific best practices](#6-context-specific-best-practices)
7. [Scene structure & pipeline hygiene](#7-scene-structure--pipeline-hygiene)
8. [Migration notes for Houdini 21](#8-migration-notes-for-houdini-21)

---

## 1. General principles

- **Stay procedural and non-destructive.** Prefer parameters, attributes and
  graph structure over baked results. If you hand-edit, do it in a way a node
  can reproduce.
- **Build for change.** Drive values with `ch()`/attributes, not magic numbers.
  Wrap reusable networks in **HDAs** with a clean parameter interface.
- **Name things.** Name nodes for intent (`scatter_debris`, not `scatter3`).
  Author `@name`/`@path` deliberately — they become USD prim paths downstream.
- **Version up, don't overwrite.** Cache and publish to versioned paths
  (`v001`, `v002`); keep `$HIPNAME`/`$OS`/`$F` in output paths.
- **Profile before optimizing.** Use the **Performance Monitor** (per-node cook
  times) and, for GPU work, the **GPU Status** pane. Optimize the node that
  actually dominates.
- **Use the built-in node if one exists.** Don't reimplement Fuse, Measure,
  Connectivity or PolyExtrude in VEX. Hand-write VEX for logic that has no node.

---

## 2. VEX essentials (quick reference)

### Choose the right "Run Over"

A wrangle's **Run Over** mode decides what the code iterates and which globals
are valid. Picking the wrong one is the most common beginner bug.

| Run Over | Key globals | Use for |
|----------|-------------|---------|
| **Detail (only once)** | `@numpt`, `@numprim` (no per-element `@P`) | one-time setup, building geometry, reductions/accumulation |
| **Points** | `@P`, `@ptnum`, `@N`, `@numpt` | most attribute work (the default) |
| **Primitives** | `@primnum`, `primpoints()` | per-face/per-curve work |
| **Vertices** | `@vtxnum`, `vertexprim()` | vertex UVs, vertex normals, seams |
| **Numbers** | `@elemnum`, `@numelem` | generate N elements with no input geometry |

### Attribute type prefixes — always type your binds

An untyped `@foo` is a float. Binding a vector/quaternion without a prefix
silently keeps only the first component. Prefix on first use; reuse bare after.

| Prefix | Type | Example |
|--------|------|---------|
| `i@` | int | `i@id` |
| `f@` | float | `f@mask` |
| `u@` | vector2 | `u@uv` |
| `v@` | vector (3) | `v@N` |
| `p@` | vector4 / quaternion | `p@orient` |
| `2@` `3@` `4@` | matrix2 / matrix3 / matrix4 | `3@rot`, `4@xform` |
| `s@` | string | `s@name` |
| `i[]@` `f[]@` `v[]@` `s[]@` | arrays | `i[]@nbrs` |

### Globals worth knowing

`@P` `@N` `@Cd` `@v` (position/normal/colour/velocity) · `@ptnum`/`@numpt`,
`@primnum`/`@numprim`, `@vtxnum` (element indices/counts) · `@Time` `@Frame`
`@TimeInc` (animation/sim time) · `@ix @iy @iz @resx` (Volume Wrangle voxel
coords) · `@group_NAME` (read/write group membership).

### Reading from multiple inputs

```vex
// Second wrangle input is index 1. Two equivalent reads:
vector p2 = point(1, "P", @ptnum);
vector p3 = v@opinput1_P;          // shorthand bind to input 1's P
// Nearest point on input-1 surface:
int prim; vector uv;
float dist = xyzdist(1, @P, prim, uv);
```

---

## 3. VEX best practices

### Attributes and types
- **Type every bind** (see the table above). `v@up = {0,1,0};` — not `@up`.
- **Create only the attributes you need.** Each written attribute costs memory
  and rides downstream. Use a local variable for scratch values:
  ```vex
  // GOOD: scratch stays local, no attribute created
  float d = length(@P - chv("center"));
  @Cd = chramp("falloff", d);
  ```
- **Read, don't recompute.** If a Normal SOP made `@N`, use it.

### Channels and ramps
- **Read `ch*()` into locals** when used more than once; it documents intent:
  ```vex
  float amp  = chf("amp");
  vector ctr = chv("center");
  ```
- Prefer `chramp("name", t)` for art-directable curves over hand-rolled remaps.

### Performance
- **A wrangle runs once per element, in parallel.** Never assume order; never
  read another element's *concurrent* write.
- **Use spatial lookups, not brute force.** Point clouds and `nearpoints()` are
  the difference between O(n log n) and O(n²):
  ```vex
  int h = pcopen(0, "P", @P, chf("radius"), chi("maxpts"));
  @density = pcnumfound(h);
  pcclose(h);                 // always close the handle
  ```
- **Compare squared distances** to skip a `sqrt`:
  ```vex
  if (length2(@P - target) < r * r) { ... }   // not distance() < r
  ```
- **Hoist invariants out of loops**; compute constants once.
- **Initialize array/string detail attributes** to length before index-writing,
  or build with `append()`.

### Editing geometry
- `addpoint`/`addprim`/`addvertex`/`setpointattrib` are thread-safe and return
  fresh element numbers. Build topology in a **Detail** wrangle when you need a
  single coherent pass:
  ```vex
  // Detail Wrangle: lay points along X and stitch them into one polyline.
  int prev = -1;
  for (int i = 0; i < chi("count"); i++) {
      int pt = addpoint(0, set(i * chf("step"), 0, 0));
      if (prev >= 0) {
          int pr = addprim(0, "polyline");
          addvertex(0, pr, prev);
          addvertex(0, pr, pt);
      }
      prev = pt;
  }
  ```
- `removepoint(0, @ptnum)` deletes the current point;
  `removeprim(0, @primnum, 1)` deletes the prim **and** its points (pass `0` to
  keep the points). Prefer these over building delete groups by hand.

### Groups
```vex
// Read and write group membership directly with the i@group_ prefix.
if (@P.y > chf("h")) i@group_top = 1;
```

### Randomness
- **Decorrelate streams** with offset seeds; identical seeds correlate results:
  ```vex
  @pscale  = fit01(rand(@ptnum),       0.8, 1.2);
  p@orient = quaternion(rand(@ptnum + 1.7) * 2 * PI, {0,1,0});
  ```

### Reuse
- Factor shared logic into VEX functions and `#include` headers; keep wrangles
  single-concept. A 200-line wrangle is usually several nodes.

---

## 4. Common anti-patterns (before → after)

**Untyped vector bind** — only `x` survives:
```vex
@up = {0, 1, 0};      // BAD: @up is a float
v@up = {0, 1, 0};     // GOOD
```

**Brute-force nearest neighbour** — O(n²):
```vex
// BAD
float best = 1e18; int bp = -1;
for (int i = 0; i < @numpt; i++) {
    float d = distance(@P, point(0, "P", i));
    if (d < best) { best = d; bp = i; }
}
// GOOD: spatial query
int near[] = nearpoints(0, @P, 1e9, 2);   // self + 1 neighbour
int bp2 = near[1];
```

**`sqrt` in a distance test**:
```vex
if (distance(@P, c) < r) ...        // BAD
if (length2(@P - c) < r * r) ...    // GOOD
```

**Wrong Run Over** — displacing in Detail mode touches only point 0:
```vex
// BAD: Run Over = Detail
@P += @N * chf("amp");
// GOOD: Run Over = Points  (then @P/@N are per-point)
```

**APEX RunVex using `@`** — RunVex has no attribute syntax:
```vex
@result = a + b;     // BAD
result = a + b;      // GOOD: named output port
```

**Leaking a point-cloud handle**:
```vex
int h = pcopen(0, "P", @P, r, n);
... // BAD: no pcclose(h)
pcclose(h);          // GOOD
```

---

## 5. VEX vs OpenCL vs Compiled Blocks

No "always faster" answer — choose by data shape:

| Use | When |
|-----|------|
| **VEX (wrangle)** | The default. CPU, flexible, full geometry API, readable. Right for nearly all SOP attribute work. |
| **OpenCL (SOP / DOP microsolver)** | Heavy, *uniform*, arithmetic-dominated work over very large element counts or volumes — especially simulation inner loops. GPU wins when data is large and the kernel is simple. Not for file I/O, recursion, or sparse random access. |
| **Compiled Blocks** | When a For-Each / SOP loop must run multithreaded and allocation-free over many pieces. Compile-friendly nodes only. |

OpenCL tips: copy `@bindings` into local variables (each bind can re-fetch),
keep kernels branch-light, and watch host↔device transfer — copying data to the
GPU every frame can erase the compute win. Profile with the **GPU Status** pane.

> Rule of thumb: write it in VEX first. Port the *proven* bottleneck to OpenCL
> only after the Performance Monitor says that node dominates.

---

## 6. Context-specific best practices

### 6.1 Procedural modeling (SOP)
- Establish topology early; carry **stable ids** (`@name`, `@id`, connectivity
  `@class`) so downstream nodes and USD stay consistent across cooks.
- Use **groups** to scope operations; prefer attribute-driven group creation.
- Keep heavy geometry **packed** until you must unpack — it slashes memory and
  speeds copies and the viewport.
- Instancing prep:
  ```vex
  // Point Wrangle feeding Copy to Points / USD Instancer.
  p@orient = dihedral({0,1,0}, normalize(@N));   // stand up along the normal
  @pscale  = fit01(rand(@ptnum), 0.8, 1.2);
  ```

### 6.2 Simulation, solvers and MPM (H21)
- **MPM** (multi-material solver, extended in 21 with debris emission and
  surface tension): set initial state on the **source** before the solver;
  apply custom forces in a **Geometry/Gas Field Wrangle** stepping the sim, and
  **scale impulses by `f@TimeInc`** so behaviour is substep-independent:
  ```vex
  // Geometry Wrangle inside a solver: outward radial impulse.
  vector dir = @P - chv("center");
  v@v += normalize(dir) * chf("strength") * f@TimeInc;
  ```
- Prefer **deterministic** setups (fixed seeds, fixed substeps) so caches are
  reproducible. Cache sims to disk before lighting; never re-sim downstream.
- Pick the right solver: **Vellum** (cloth/soft constraints), **MPM**
  (granular/snow/sand/multi-material continua), **Otis** (new in 21,
  GPU muscle/tissue), Pyro (now with a sparse GPU path via Copernicus).

### 6.3 Look development — materials (CVEX / MaterialX / Karma)
- **Author materials in MaterialX** for Karma CPU **and** XPU parity and USD
  portability; reach for the `mtlx` VOPs over legacy-only setups.
- Drop to **CVEX / Snippet VOPs** for procedural logic a node graph makes clumsy
  — keep it branch-light, it runs per shading sample:
  ```vex
  // Snippet VOP: fresnel rim. I is the incident ray; -I faces the camera.
  float f = pow(1.0 - clamp(dot(normalize(N), normalize(-I)), 0, 1),
                chf("rim_power"));
  Cf += f * chv("rim_color");
  ```
- **Displacement:** offset `P` along `N`, then recompute normals (Compute Normal
  VOP or the displace node option) or lighting won't follow the surface.
- Build procedural **textures in Copernicus (COPs)** — GPU, production-ready in
  21 for most uses — instead of a DCC round-trip.

### 6.4 Lighting (Solaris / USD / Karma)
- Light in **Solaris (LOPs)** with USD lights; keep lighting in its own **layer**
  so it composes over layout/FX without editing them.
- Use **light linking** and **light filters** instead of duplicating lights.
- Use **Karma XPU** for interactive look-dev; validate finals on the renderer
  you ship. Set **render purposes** (`proxy`/`render`) so viewports stay fast.
- Keep per-light variation data-driven (instance attributes: intensity, colour,
  orient) rather than hand-placing near-identical lights.

### 6.5 Solaris / USD
- **Think in layers and opinions.** Each department edits its own layer; use
  sublayers/references and let stronger opinions override — never destructively
  flatten a shared stage.
- Keep **prim paths stable** — derive `@path`/`@name` from persistent ids, not
  primitive number, so references/overrides survive recooks:
  ```vex
  // Primitive Wrangle before SOP Import: stable USD paths from a piece id.
  s@path = sprintf("/geo/piece_%d", i@piece);
  ```
- Use **instancing** for large populations; tag **purposes** and **kinds**.
- Reserve SOP VEX for the geometry/attribute prep that *feeds* USD import; do
  stage edits with LOP nodes / Python LOPs.

### 6.6 APEX (rigging & animation, production in H21)
- Build the skeleton in **KineFX (SOPs)**, then a procedural **APEX rig** on top
  with rig components — the intended H21 workflow, now aimed at animators
  (drag-and-drop viewport rig builder), not only TDs.
- APEX is a **separate graph language**. A **RunVex** node has **no `@`
  syntax** — it works on **named inputs/outputs** only. Don't paste SOP wrangle
  code into it:
  ```vex
  // APEX RunVex. Inputs: vector a, b; float t. Output: vector result.
  result = lerp(a, b, t);
  ```
- Keep APEX graphs modular; APEX favours many small fast ops over monoliths.

### 6.7 TOPs / PDG
- Orchestration is **Python-first** (work items, attributes, scheduling); VEX
  appears only in the SOP cooks a work item triggers.
- Drive variation with **work-item attributes / wedges** read on the geometry
  side, and stamp the work-item index onto outputs for later identification.
- Make tasks **deterministic and idempotent** so dirty/recook and distributed
  scheduling stay correct.

### 6.8 Copernicus (COPs)
- Use it for **GPU image processing and procedural texturing**; production-ready
  in 21 except advanced compositing. Keep networks GPU-friendly (avoid CPU
  round-trips); leverage its sparse GPU solvers and interactive handles.

---

## 7. Scene structure & pipeline hygiene

- **One responsibility per HDA.** Version HDA definitions; lock published ones.
- **Cache deliberately.** File Cache SOPs to versioned `.bgeo.sc`/USD; don't
  recompute heavy upstream every frame.
- **Keep contexts separate**: model/FX in SOPs → assemble/light in Solaris →
  render in Karma. Don't blur the boundary.
- **Reproducibility**: pin seeds, substeps and ranges; avoid wallclock-driven
  values in anything cached.
- **Source-control friendliness**: prefer HDAs and small `.hip` deltas;
  externalize large caches; commit `.hip`/USD, not gigabytes of geometry.

---

## 8. Migration notes for Houdini 21

- **Viewport is Vulkan-only** — OpenGL is gone. Update GPU drivers; verify
  custom viewport tooling.
- **APEX rigging is production** — new rigs should target the APEX workflow over
  legacy auto-rig / CHOP-heavy setups.
- **MPM extended** — revisit granular/multi-material setups that used
  grains/FLIP where MPM now fits better (debris, surface tension).
- **Copernicus replaces the old COPs** for texturing/image work.
- **Karma** is the path forward over Mantra; author materials in MaterialX for
  CPU/XPU parity.

---

## Sources

- [SideFX: What's new in Houdini 21 — VEX & OpenCL](https://www.sidefx.com/docs/houdini/news/21/vex.html)
- [SideFX: OpenCL for VEX users](https://www.sidefx.com/docs/houdini/vex/ocl.html)
- [SideFX: Attributes and the @ syntax in VEX snippets](https://www.sidefx.com/docs/houdini/vex/snippets.html)
- [SideFX: MPM Solver](https://www.sidefx.com/docs/houdini/nodes/sop/mpmsolver.html), [Gas Field Wrangle](https://www.sidefx.com/docs/houdini/nodes/dop/gasfieldwrangle.html)
- [SideFX: APEX nodes](https://www.sidefx.com/docs/houdini/nodes/apex/index.html)
- [CG Channel: Houdini 21 key features](https://www.cgchannel.com/2025/08/sidefx-just-released-houdini-21-check-out-its-5-key-features/)
- [Digital Production: Houdini 21 — Otto, Otis, Copernicus and 300+ features](https://digitalproduction.com/2025/08/05/houdini-21-otto-otis-copernicus-and-300-new-features-no-really/)
