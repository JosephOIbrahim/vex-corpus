# VEX Corpus Processing Blueprint
## Ollama Model Layer Specification v1.0

> **Target Models:** Nemotron-Mini-4B (Tier 1), Minitron-8B (Tier 2)
> **Hardware:** RTX 4090 (24GB VRAM), Threadripper PRO 7965WX, 128GB RAM
> **Purpose:** Bulk processing layer for VEX training corpus generation

---

# SECTION 1: DOMAIN KNOWLEDGE PRIMER

## 1.1 What is VEX?

VEX (Vector Expression Language) is Houdini's high-performance scripting language for manipulating geometry, attributes, and simulations. It runs in parallel across geometry elements.

### Execution Contexts

VEX runs in different **contexts** that determine which geometry element is being processed:

| Context | Element Variable | Count Variable | Use Case |
|---------|-----------------|----------------|----------|
| **point** | `@ptnum` | `@numpt` | Per-point operations (most common) |
| **primitive** | `@primnum` | `@numprim` | Per-face/curve operations |
| **vertex** | `@vtxnum` | `@numvtx` | Per-vertex (corner) operations |
| **detail** | N/A | N/A | Single execution, global operations |

### Attribute Syntax

```vex
// Reading attributes (@ prefix)
vector pos = @P;           // Built-in position
float scale = @pscale;     // Custom float attribute
int id = @id;              // Custom integer attribute

// Writing attributes
@Cd = {1, 0, 0};          // Set color to red
@N = normalize(@N);        // Modify normal

// Typed declarations
float @custom_float = 0;   // Declare with type
vector @custom_vec;        // Vector attribute
int @custom_int;           // Integer attribute
```

### Common Built-in Attributes

| Attribute | Type | Context | Description |
|-----------|------|---------|-------------|
| `@P` | vector | point | Position |
| `@N` | vector | point/vertex | Normal |
| `@Cd` | vector | any | Color (RGB) |
| `@v` | vector | point | Velocity |
| `@pscale` | float | point | Point scale |
| `@id` | int | point | Stable identifier |
| `@ptnum` | int | point | Current point number |
| `@primnum` | int | prim | Current primitive number |
| `@numpt` | int | any | Total point count |
| `@numprim` | int | any | Total primitive count |

### Key VEX Functions by Category

**Geometry Query:**
- `point(input, "attr", ptnum)` — Read point attribute
- `prim(input, "attr", primnum)` — Read primitive attribute
- `vertex(input, "attr", linearvertex)` — Read vertex attribute
- `detail(input, "attr", 0)` — Read detail attribute
- `npoints(input)` — Point count
- `nprimitives(input)` — Primitive count

**Geometry Modification:**
- `setpointattrib(geohandle, "attr", ptnum, value)` — Set point attribute
- `setprimattrib(geohandle, "attr", primnum, value)` — Set primitive attribute
- `addpoint(geohandle, position)` — Create point
- `addprim(geohandle, "poly", pt0, pt1, ...)` — Create primitive
- `removepoint(geohandle, ptnum)` — Delete point
- `removeprim(geohandle, primnum, andpoints)` — Delete primitive

**Point Cloud Operations:**
- `pcopen(file, "P", pos, radius, maxpts)` — Open point cloud search
- `pcfind(input, "P", pos, radius, maxpts)` — Find nearby points (returns array)
- `pcfind_radius(input, "P", "pscale", pos, radius, maxpts)` — Variable radius search
- `pcfilter(handle, "attr")` — Filter/average attribute from found points
- `pcclose(handle)` — Close point cloud handle (IMPORTANT: prevent memory leak)
- `nearpoints(input, pos, radius, maxpts)` — Simple neighbor search

**Noise Functions:**
- `noise(pos)` — Perlin noise (0-1 range)
- `snoise(pos)` — Signed Perlin noise (-1 to 1)
- `onoise(pos)` — Original Houdini noise
- `curlnoise(pos)` — Divergence-free curl noise (returns vector)
- `random(seed)` — Deterministic random

**Math:**
- `fit(val, omin, omax, nmin, nmax)` — Remap value
- `clamp(val, min, max)` — Constrain to range
- `lerp(a, b, t)` — Linear interpolation
- `smooth(min, max, val)` — Smooth interpolation
- `length(vec)` — Vector magnitude
- `normalize(vec)` — Unit vector
- `dot(a, b)` — Dot product
- `cross(a, b)` — Cross product

**Channel References:**
- `ch("parm")` — Float parameter
- `chf("parm")` — Float parameter (explicit)
- `chi("parm")` — Integer parameter
- `chv("parm")` — Vector parameter
- `chs("parm")` — String parameter

---

## 1.2 Common VEX Patterns

### Pattern: Neighbor Averaging
```vex
// Smooth attribute by averaging neighbors
int pts[] = nearpoints(0, @P, ch("radius"));
vector sum = {0,0,0};
foreach(int pt; pts) {
    sum += point(0, "Cd", pt);
}
@Cd = sum / max(len(pts), 1);
```

### Pattern: Point Cloud Relaxation
```vex
// Push points apart
int handle = pcopen(0, "P", @P, ch("radius"), chi("maxpts"));
vector avg = pcfilter(handle, "P");
pcclose(handle);
@P = lerp(@P, avg, ch("blend"));
```

### Pattern: Gradient Ascent
```vex
// Move uphill on scalar field
float eps = 0.001;
vector grad;
grad.x = point(1, "density", @ptnum) - point(1, "density_x", @ptnum);
grad.y = point(1, "density", @ptnum) - point(1, "density_y", @ptnum);
grad.z = point(1, "density", @ptnum) - point(1, "density_z", @ptnum);
@P += normalize(grad) * ch("step");
```

### Pattern: Attribute Transfer
```vex
// Transfer attribute from nearest point on input 1
int nearpt = nearpoints(1, @P, 1e9, 1)[0];
@Cd = point(1, "Cd", nearpt);
```

---

## 1.3 Common VEX Bugs

| Bug Type | Code Example | Problem | Fix |
|----------|--------------|---------|-----|
| **context_confusion** | `@primnum` in point wrangle | Wrong element variable | Use `@ptnum` |
| **uninitialized_attr** | `@Cd = @Cd * 0.5` (no Cd exists) | Reading undefined attr | Initialize or check `hasattrib()` |
| **type_mismatch** | `@P = length(@P)` | Float assigned to vector | `@P = normalize(@P) * length(@P)` |
| **off_by_one** | `point(0, "P", @numpt)` | Index out of bounds | Use `@numpt - 1` |
| **resource_leak** | `pcopen()` without `pcclose()` | Memory leak | Always call `pcclose(handle)` |
| **wrong_context_write** | `setdetailattrib()` in point wrangle | Runs per-point, wasteful | Move to detail wrangle |
| **missing_semicolon** | `@P = {0,0,0}` (no semicolon) | Syntax error | Add `;` |
| **integer_division** | `float f = 1/2` | Evaluates to 0 | Use `1.0/2.0` or cast |

---

# SECTION 2: TASK DEFINITIONS

## 2.1 Task Registry

### TIER 1 TASKS (Nemotron-Mini-4B)
High-volume, deterministic classification and extraction.

| Task ID | Input | Output | Consensus |
|---------|-------|--------|-----------|
| `classify_wrangle` | VEX code | context type | Optional |
| `extract_attributes` | VEX code | attr lists | No |
| `extract_functions` | VEX code | function list | No |
| `detect_bugs` | VEX code | potential bugs | Optional |
| `estimate_complexity` | VEX code | O notation | Optional |
| `classify_topic` | VEX code | topic bucket | Yes |

### TIER 2 TASKS (Minitron-8B)
Generative tasks requiring reasoning and natural language.

| Task ID | Input | Output | Consensus |
|---------|-------|--------|-----------|
| `generate_prompt` | code + context | instruction text | No |
| `generate_explanation` | code + context | explanation text | No |
| `inject_bug` | code + bug_type | buggy code + fix | Optional |
| `paraphrase_prompt` | prompt | variant prompt | No |
| `rate_difficulty` | code + context | 1-10 rating | Yes |
| `infer_context` | code | full context spec | No |

---

## 2.2 Task Specifications

### TASK: classify_wrangle

**Purpose:** Determine the execution context of VEX code.

**Input Schema:**
```json
{
  "code": "string (VEX source code)"
}
```

**Output Schema:**
```json
{
  "context": "point | prim | vertex | detail | unknown",
  "confidence": 0.0-1.0,
  "evidence": ["array of indicators found"]
}
```

**Decision Rules:**
1. If `@ptnum` or `setpointattrib` present → likely **point**
2. If `@primnum` or `setprimattrib` present → likely **prim**
3. If `@vtxnum` or `setvertexattrib` present → likely **vertex**
4. If only `detail()` reads or `setdetailattrib` without loops → likely **detail**
5. If `@P`, `@N`, `@Cd` without other indicators → likely **point** (most common)
6. Conflicting signals → report **unknown** with low confidence

**Examples:**

Input:
```vex
@P += curlnoise(@P * ch("freq")) * ch("amp");
```
Output:
```json
{
  "context": "point",
  "confidence": 0.85,
  "evidence": ["@P read/write", "no prim/vertex indicators", "typical point operation"]
}
```

Input:
```vex
int pts[] = primpoints(0, @primnum);
vector centroid = {0,0,0};
foreach(int pt; pts) centroid += point(0, "P", pt);
@P = centroid / len(pts);
```
Output:
```json
{
  "context": "prim",
  "confidence": 0.95,
  "evidence": ["@primnum used", "primpoints() call", "iterating prim vertices"]
}
```

---

### TASK: extract_attributes

**Purpose:** Identify all attributes read from and written to.

**Input Schema:**
```json
{
  "code": "string (VEX source code)"
}
```

**Output Schema:**
```json
{
  "reads": [
    {
      "name": "string",
      "type": "int | float | vector | vector2 | vector4 | matrix3 | matrix | string | unknown",
      "context": "point | prim | vertex | detail | unknown",
      "builtin": true/false
    }
  ],
  "writes": [
    {
      "name": "string",
      "type": "...",
      "context": "...",
      "builtin": true/false
    }
  ],
  "channels": [
    {
      "name": "string",
      "type": "float | int | vector | string",
      "function": "ch | chf | chi | chv | chs"
    }
  ]
}
```

**Identification Rules:**

1. **Attribute reads:** `@attrname` on right side of assignment or in expressions
2. **Attribute writes:** `@attrname =` or `@attrname +=` etc.
3. **Typed attributes:** Look for `int @name`, `float @name`, `vector @name` declarations
4. **Built-in attributes:** P, N, Cd, v, up, pscale, id, ptnum, primnum, vtxnum, numpt, numprim, etc.
5. **Function-based access:** `point()`, `prim()`, `vertex()`, `detail()` calls
6. **Channel refs:** `ch()`, `chf()`, `chi()`, `chv()`, `chs()` calls

**Examples:**

Input:
```vex
float density = point(1, "density", @ptnum);
@Cd = fit(density, 0, 1, {0,0,1}, {1,0,0});
@pscale = ch("scale") * density;
```
Output:
```json
{
  "reads": [
    {"name": "density", "type": "float", "context": "point", "builtin": false},
    {"name": "ptnum", "type": "int", "context": "point", "builtin": true}
  ],
  "writes": [
    {"name": "Cd", "type": "vector", "context": "point", "builtin": true},
    {"name": "pscale", "type": "float", "context": "point", "builtin": true}
  ],
  "channels": [
    {"name": "scale", "type": "float", "function": "ch"}
  ]
}
```

---

### TASK: extract_functions

**Purpose:** Identify all VEX function calls.

**Input Schema:**
```json
{
  "code": "string"
}
```

**Output Schema:**
```json
{
  "functions": [
    {
      "name": "string",
      "category": "geometry_query | geometry_modify | math | noise | string | channel | attribute | control_flow | other",
      "count": integer
    }
  ],
  "has_loops": true/false,
  "has_conditionals": true/false,
  "custom_functions": ["names of user-defined functions"]
}
```

**Category Reference:**

- **geometry_query:** point, prim, vertex, detail, npoints, nprimitives, nearpoints, pcopen, pcfind, primpoints, etc.
- **geometry_modify:** setpointattrib, setprimattrib, addpoint, addprim, removepoint, removeprim, etc.
- **math:** sin, cos, sqrt, pow, abs, floor, ceil, clamp, fit, lerp, smooth, length, normalize, dot, cross, etc.
- **noise:** noise, snoise, onoise, curlnoise, random, etc.
- **string:** sprintf, split, join, match, re_match, etc.
- **channel:** ch, chf, chi, chv, chs, chramp, etc.
- **attribute:** hasattrib, getattrib, setattrib, attribtype, etc.
- **control_flow:** if, else, for, foreach, while, break, continue, return

---

### TASK: detect_bugs

**Purpose:** Identify potential bugs or anti-patterns.

**Input Schema:**
```json
{
  "code": "string",
  "context": "point | prim | vertex | detail | unknown (optional)"
}
```

**Output Schema:**
```json
{
  "bugs": [
    {
      "type": "context_confusion | uninitialized_attr | type_mismatch | off_by_one | resource_leak | wrong_context_write | integer_division | other",
      "severity": "error | warning | info",
      "location": "line number or description",
      "description": "what's wrong",
      "suggestion": "how to fix"
    }
  ],
  "overall_quality": "clean | minor_issues | major_issues"
}
```

**Detection Rules:**

1. **resource_leak:** `pcopen()` without corresponding `pcclose()`
2. **context_confusion:** `@ptnum` in prim context, `@primnum` in point context
3. **uninitialized_attr:** Reading `@attr` that's never written and not builtin
4. **type_mismatch:** Vector = scalar, scalar = vector patterns
5. **off_by_one:** Array/point access at `@numpt` instead of `@numpt-1`
6. **wrong_context_write:** `setdetailattrib()` inside point/prim wrangle
7. **integer_division:** `int/int` without float cast

---

### TASK: estimate_complexity

**Purpose:** Estimate algorithmic complexity.

**Input Schema:**
```json
{
  "code": "string"
}
```

**Output Schema:**
```json
{
  "complexity": "O(1) | O(n) | O(n log n) | O(n²) | O(n³) | unknown",
  "confidence": 0.0-1.0,
  "reasoning": "explanation",
  "bottlenecks": ["list of expensive operations"]
}
```

**Complexity Indicators:**

- **O(1):** No loops, no point cloud searches, constant operations
- **O(n):** Single pass over points, bounded loops
- **O(n log n):** `pcfind` with bounded radius (KD-tree search)
- **O(n²):** Nested loops over all points, unbounded `pcopen`, `nearpoints` without radius limit
- **O(n³):** Triple nested loops, matrix operations over all points

---

### TASK: classify_topic

**Purpose:** Assign curriculum topic bucket(s).

**Input Schema:**
```json
{
  "code": "string",
  "task_description": "string (optional)"
}
```

**Output Schema:**
```json
{
  "primary_topic": "point_cloud_ops | optimization_patterns | field_analysis | flow_visualization | edge_topology | subdivision_surfaces | attribute_operations | debugging_patterns",
  "secondary_topics": ["additional relevant topics"],
  "confidence": 0.0-1.0,
  "indicators": ["why this classification"]
}
```

**Topic Definitions:**

- **point_cloud_ops:** pcopen, pcfind, nearpoints, relaxation, neighbor queries
- **optimization_patterns:** Caching, reducing complexity, vectorization
- **field_analysis:** Gradients, ascent/descent, scalar field sampling
- **flow_visualization:** Advection, streamlines, curl noise, flow fields
- **edge_topology:** Neighbors, half-edges, connectivity, mesh traversal
- **subdivision_surfaces:** Limit surface sampling, subdivision patterns
- **attribute_operations:** Transfer, blending, groups, promotion
- **debugging_patterns:** Error handling, validation, common fixes

---

### TASK: generate_prompt

**Purpose:** Generate natural language instruction for given code.

**Input Schema:**
```json
{
  "code": "string",
  "task": "string (brief description)",
  "context": {
    "wrangle_type": "point | prim | vertex | detail",
    "input_geo": "description of expected input",
    "output_attrs": ["list of created attributes"]
  },
  "style": "imperative | question | scenario"
}
```

**Output Schema:**
```json
{
  "prompt": "string (natural language instruction)",
  "alternative_phrasings": ["2-3 other ways to ask"]
}
```

**Style Guidelines:**

- **imperative:** "Write VEX to...", "Create a wrangle that...", "Implement..."
- **question:** "How do I...?", "What's the VEX for...?", "How can I..."
- **scenario:** "I have geometry with X. I need to Y. Write VEX that..."

**Quality Requirements:**

- Be specific about inputs and outputs
- Mention key constraints or requirements
- Avoid implementation details in the prompt
- Keep under 100 words for imperative style

---

### TASK: generate_explanation

**Purpose:** Generate educational explanation of VEX code.

**Input Schema:**
```json
{
  "code": "string",
  "task": "string",
  "context": {
    "wrangle_type": "string",
    "input_geo": "string",
    "output_attrs": ["strings"]
  },
  "audience": "beginner | intermediate | expert"
}
```

**Output Schema:**
```json
{
  "explanation": "string (2-4 paragraphs)",
  "key_concepts": ["list of VEX concepts used"],
  "gotchas": ["potential pitfalls or important notes"]
}
```

**Audience Adaptation:**

- **beginner:** Explain each function, avoid jargon, step-by-step
- **intermediate:** Focus on approach and why, assume VEX familiarity
- **expert:** Discuss tradeoffs, performance, alternatives

---

### TASK: inject_bug

**Purpose:** Create buggy variant for debug training.

**Input Schema:**
```json
{
  "code": "string (correct VEX)",
  "bug_type": "context_confusion | uninitialized_attr | type_mismatch | off_by_one | resource_leak | wrong_context_write | integer_division | logic_error"
}
```

**Output Schema:**
```json
{
  "buggy_code": "string (VEX with bug injected)",
  "bug_location": "line number or identifier",
  "bug_description": "what the bug does wrong",
  "symptoms": "what errors or incorrect behavior this causes",
  "fix_description": "how to correct it",
  "difficulty": 1-10
}
```

**Bug Injection Rules:**

1. Make exactly ONE change to inject the bug
2. The bug must be plausible (something a real user might do)
3. The bug must be detectable (causes error or wrong output)
4. Preserve overall code structure
5. Document the exact change made

---

### TASK: rate_difficulty

**Purpose:** Assess difficulty level of VEX sample.

**Input Schema:**
```json
{
  "code": "string",
  "task": "string (optional)",
  "context": {} (optional)
}
```

**Output Schema:**
```json
{
  "difficulty": 1-10,
  "factors": {
    "code_length": "short | medium | long",
    "concept_count": integer,
    "advanced_features": ["list of advanced concepts"],
    "prerequisite_knowledge": ["what you need to know first"]
  },
  "reasoning": "brief explanation"
}
```

**Difficulty Scale:**

- **1-2:** Basic attribute operations, simple math
- **3-4:** Loops, conditionals, common patterns
- **5-6:** Point cloud queries, multiple inputs, moderate complexity
- **7-8:** Advanced algorithms, optimization, custom functions
- **9-10:** Expert-level, novel techniques, deep Houdini knowledge

---

### TASK: infer_context

**Purpose:** Generate complete execution context specification.

**Input Schema:**
```json
{
  "code": "string"
}
```

**Output Schema:**
```json
{
  "wrangle_type": "point | prim | vertex | detail",
  "wrangle_confidence": 0.0-1.0,
  "inputs": [
    {
      "index": 0,
      "geo_type": "points | mesh | curves | volume | any",
      "point_count_hint": "small (<1K) | medium (1K-100K) | large (>100K) | any",
      "required_attrs": [
        {"name": "string", "type": "string", "description": "string"}
      ]
    }
  ],
  "outputs": [
    {"name": "string", "type": "string", "description": "string"}
  ],
  "channels": [
    {"name": "string", "type": "string", "default": "value", "range": [min, max]}
  ],
  "warnings": ["potential issues with this code"]
}
```

---

# SECTION 3: PROCESSING PROTOCOLS

## 3.1 Response Format

**ALL responses must be valid JSON.** No markdown, no explanations outside JSON.

**Standard Response Wrapper:**
```json
{
  "task_id": "string (from input)",
  "status": "success | error | uncertain",
  "result": { ... task-specific output ... },
  "confidence": 0.0-1.0,
  "processing_notes": "optional notes about edge cases"
}
```

**Error Response:**
```json
{
  "task_id": "string",
  "status": "error",
  "error_type": "invalid_input | parse_error | unknown_pattern | insufficient_context",
  "error_message": "description",
  "partial_result": { ... if any ... }
}
```

## 3.2 Confidence Calibration

Report confidence honestly:

- **0.9-1.0:** Very certain, clear indicators, unambiguous
- **0.7-0.89:** Confident, strong indicators, minor ambiguity
- **0.5-0.69:** Moderate, some indicators, notable uncertainty
- **0.3-0.49:** Low, weak indicators, significant ambiguity
- **0.0-0.29:** Very uncertain, guessing, flag for review

**When uncertain:**
- Still provide best-effort output
- Flag in `processing_notes`
- Lower `confidence` score
- Consider returning `"uncertain"` variants in lists

## 3.3 Batch Processing

For batch inputs, process each item independently:

**Batch Input:**
```json
{
  "task": "classify_wrangle",
  "batch": [
    {"id": "sample_001", "code": "..."},
    {"id": "sample_002", "code": "..."}
  ]
}
```

**Batch Output:**
```json
{
  "results": [
    {"id": "sample_001", "status": "success", "result": {...}},
    {"id": "sample_002", "status": "success", "result": {...}}
  ],
  "batch_stats": {
    "total": 2,
    "success": 2,
    "error": 0,
    "avg_confidence": 0.87
  }
}
```

## 3.4 Consensus Protocol

When running multiple models for consensus:

1. Each model processes independently
2. Results are compared on key fields
3. Majority vote determines final output
4. Disagreements lower overall confidence
5. Strong disagreement flags for human review

**Consensus Fields by Task:**

| Task | Consensus Field(s) |
|------|-------------------|
| classify_wrangle | context |
| classify_topic | primary_topic |
| rate_difficulty | difficulty (within ±2) |
| detect_bugs | bug types found |
| estimate_complexity | complexity class |

---

# SECTION 4: FEW-SHOT EXAMPLES

## 4.1 classify_wrangle Examples

**Example 1: Clear Point Context**

Input:
```json
{"code": "@P += @N * ch(\"offset\");"}
```

Output:
```json
{
  "task_id": "example",
  "status": "success",
  "result": {
    "context": "point",
    "confidence": 0.95,
    "evidence": ["@P write", "@N read", "typical point displacement pattern"]
  },
  "confidence": 0.95
}
```

**Example 2: Primitive Context**

Input:
```json
{"code": "int pts[] = primpoints(0, @primnum);\nsetprimattrib(0, \"area\", @primnum, len(pts));"}
```

Output:
```json
{
  "task_id": "example",
  "status": "success",
  "result": {
    "context": "prim",
    "confidence": 0.98,
    "evidence": ["@primnum used", "primpoints() call", "setprimattrib() call"]
  },
  "confidence": 0.98
}
```

**Example 3: Ambiguous/Detail Context**

Input:
```json
{"code": "int total = npoints(0);\nsetdetailattrib(0, \"count\", total);"}
```

Output:
```json
{
  "task_id": "example",
  "status": "success",
  "result": {
    "context": "detail",
    "confidence": 0.75,
    "evidence": ["setdetailattrib() call", "no element iteration", "aggregation pattern"]
  },
  "confidence": 0.75,
  "processing_notes": "Could also run in point context but would be inefficient"
}
```

---

## 4.2 extract_attributes Examples

**Example 1: Multiple Attributes**

Input:
```json
{"code": "vector rest = point(1, \"rest\", @ptnum);\nfloat blend = ch(\"blend\");\n@P = lerp(@P, rest, blend);\n@Cd = {blend, 1-blend, 0};"}
```

Output:
```json
{
  "task_id": "example",
  "status": "success",
  "result": {
    "reads": [
      {"name": "rest", "type": "vector", "context": "point", "builtin": false},
      {"name": "ptnum", "type": "int", "context": "point", "builtin": true},
      {"name": "P", "type": "vector", "context": "point", "builtin": true}
    ],
    "writes": [
      {"name": "P", "type": "vector", "context": "point", "builtin": true},
      {"name": "Cd", "type": "vector", "context": "point", "builtin": true}
    ],
    "channels": [
      {"name": "blend", "type": "float", "function": "ch"}
    ]
  },
  "confidence": 0.95
}
```

---

## 4.3 generate_explanation Examples

**Example 1: Intermediate Audience**

Input:
```json
{
  "code": "int handle = pcopen(0, \"P\", @P, ch(\"radius\"), chi(\"maxpts\"));\nvector avg = pcfilter(handle, \"P\");\npcclose(handle);\n@P = lerp(@P, avg, ch(\"blend\"));",
  "task": "Point cloud relaxation",
  "audience": "intermediate"
}
```

Output:
```json
{
  "task_id": "example",
  "status": "success",
  "result": {
    "explanation": "This implements point cloud relaxation, a technique for smoothing point distributions. The algorithm finds neighboring points within a search radius using pcopen(), then calculates their average position with pcfilter(). The original position is blended toward this average, pushing clustered points apart and filling gaps.\n\nThe blend parameter controls relaxation strength—higher values cause faster convergence but may overshoot. Multiple iterations with low blend values produce smoother results. The pcclose() call is critical for preventing memory leaks when processing many points.",
    "key_concepts": ["point cloud search", "pcopen/pcfilter/pcclose pattern", "position averaging", "iterative relaxation"],
    "gotchas": ["Always close pcopen handles", "High maxpts with large radius is O(n²)", "Boundary points move inward without special handling"]
  },
  "confidence": 0.9
}
```

---

## 4.4 inject_bug Examples

**Example 1: Resource Leak**

Input:
```json
{
  "code": "int handle = pcopen(0, \"P\", @P, 1.0, 100);\nvector avg = pcfilter(handle, \"P\");\npcclose(handle);\n@P = avg;",
  "bug_type": "resource_leak"
}
```

Output:
```json
{
  "task_id": "example",
  "status": "success",
  "result": {
    "buggy_code": "int handle = pcopen(0, \"P\", @P, 1.0, 100);\nvector avg = pcfilter(handle, \"P\");\n@P = avg;",
    "bug_location": "line 3 (removed pcclose)",
    "bug_description": "pcopen() allocates a point cloud search handle that must be freed with pcclose(). Without closing, each point iteration leaks memory.",
    "symptoms": "Memory usage grows linearly with point count. With large point clouds (>100K), may cause out-of-memory crashes or severe slowdown.",
    "fix_description": "Add pcclose(handle) after pcfilter() and before modifying @P.",
    "difficulty": 3
  },
  "confidence": 0.95
}
```

---

# SECTION 5: QUALITY REQUIREMENTS

## 5.1 Output Validation

All outputs must pass:

1. **JSON validity:** Parseable JSON, no trailing commas, proper escaping
2. **Schema compliance:** Required fields present, correct types
3. **Value ranges:** Confidence 0-1, difficulty 1-10, valid enums
4. **Logical consistency:** Evidence supports conclusion, no contradictions
5. **Completeness:** All requested fields populated

## 5.2 Code in Output

When including VEX code in output:

1. Escape quotes properly: `\"` inside JSON strings
2. Use `\n` for newlines, not actual newlines
3. Preserve indentation as spaces
4. Verify brackets/braces are balanced

Example:
```json
{
  "buggy_code": "int handle = pcopen(0, \"P\", @P, 1.0, 100);\nvector avg = pcfilter(handle, \"P\");\n@P = avg;"
}
```

## 5.3 Graceful Degradation

When unable to complete task fully:

1. Return partial results with reduced confidence
2. Document what succeeded and what failed
3. Suggest what additional context would help
4. Never return empty or null without explanation

---

# SECTION 6: ERROR HANDLING

## 6.1 Malformed Input

If input VEX code has syntax errors:

```json
{
  "status": "success",
  "result": {
    "context": "unknown",
    "confidence": 0.3,
    "evidence": ["syntax errors detected", "partial analysis only"]
  },
  "processing_notes": "Input has unbalanced braces. Analysis based on recognizable fragments."
}
```

## 6.2 Unknown Patterns

If code uses unfamiliar patterns:

```json
{
  "status": "uncertain",
  "result": {
    "context": "point",
    "confidence": 0.4,
    "evidence": ["default assumption", "no clear indicators"]
  },
  "processing_notes": "Code pattern not recognized. Defaulting to point context. Flag for review."
}
```

## 6.3 Insufficient Context

If context is needed but not provided:

```json
{
  "status": "success",
  "result": {
    "context": "point",
    "confidence": 0.5,
    "evidence": ["ambiguous - could be point or prim"]
  },
  "processing_notes": "Context could be determined with knowledge of input geometry type."
}
```

---

# SECTION 7: OPERATIONAL PARAMETERS

## 7.1 Model Configuration

**Nemotron-Mini-4B (Tier 1):**
```json
{
  "temperature": 0.1,
  "top_p": 0.9,
  "max_tokens": 1024,
  "format": "json"
}
```

**Minitron-8B (Tier 2):**
```json
{
  "temperature": 0.3,
  "top_p": 0.95,
  "max_tokens": 2048,
  "format": "json"
}
```

## 7.2 Task Routing

| Task | Model | Timeout | Retry |
|------|-------|---------|-------|
| classify_wrangle | nemotron-mini | 10s | 2 |
| extract_attributes | nemotron-mini | 15s | 2 |
| extract_functions | nemotron-mini | 10s | 2 |
| detect_bugs | nemotron-mini | 20s | 2 |
| estimate_complexity | nemotron-mini | 10s | 2 |
| classify_topic | nemotron-mini | 15s | 2 |
| generate_prompt | minitron | 30s | 3 |
| generate_explanation | minitron | 45s | 3 |
| inject_bug | minitron | 30s | 3 |
| paraphrase_prompt | minitron | 20s | 2 |
| rate_difficulty | minitron | 15s | 2 |
| infer_context | minitron | 30s | 3 |

## 7.3 Throughput Targets

| Model | Tasks/Minute | Daily Capacity |
|-------|--------------|----------------|
| nemotron-mini | 30-50 | 40-70K |
| minitron | 10-20 | 15-30K |
| Combined | 40-70 | 55-100K |

---

# APPENDIX A: VEX FUNCTION REFERENCE

## Geometry Query Functions

| Function | Signature | Description |
|----------|-----------|-------------|
| point | `point(input, "attr", ptnum)` | Read point attribute |
| prim | `prim(input, "attr", primnum)` | Read primitive attribute |
| vertex | `vertex(input, "attr", linearvertex)` | Read vertex attribute |
| detail | `detail(input, "attr", 0)` | Read detail attribute |
| npoints | `npoints(input)` | Total points |
| nprimitives | `nprimitives(input)` | Total primitives |
| nvertices | `nvertices(input)` | Total vertices |
| primpoints | `primpoints(input, primnum)` | Points in primitive |
| pointprims | `pointprims(input, ptnum)` | Primitives using point |
| nearpoints | `nearpoints(input, pos, radius, maxpts)` | Find nearby points |
| pcopen | `pcopen(input, "P", pos, radius, maxpts)` | Open point cloud search |
| pcfind | `pcfind(input, "P", pos, radius, maxpts)` | Find points (array) |
| pcfilter | `pcfilter(handle, "attr")` | Average attribute |
| pcclose | `pcclose(handle)` | Close point cloud |

## Geometry Modification Functions

| Function | Signature | Description |
|----------|-----------|-------------|
| setpointattrib | `setpointattrib(geo, "attr", ptnum, val)` | Set point attribute |
| setprimattrib | `setprimattrib(geo, "attr", primnum, val)` | Set prim attribute |
| setvertexattrib | `setvertexattrib(geo, "attr", primnum, vtxnum, val)` | Set vertex attribute |
| setdetailattrib | `setdetailattrib(geo, "attr", val)` | Set detail attribute |
| addpoint | `addpoint(geo, pos)` | Create point |
| addprim | `addprim(geo, "poly", pt0, pt1, ...)` | Create primitive |
| addvertex | `addvertex(geo, primnum, ptnum)` | Add vertex to prim |
| removepoint | `removepoint(geo, ptnum)` | Delete point |
| removeprim | `removeprim(geo, primnum, keeppts)` | Delete primitive |

## Math Functions

| Function | Description |
|----------|-------------|
| abs, sign | Absolute value, sign |
| floor, ceil, round, trunc | Rounding |
| min, max, clamp | Range operations |
| fit, fit01, fit10, fit11 | Value remapping |
| lerp, slerp, smooth | Interpolation |
| sin, cos, tan, asin, acos, atan, atan2 | Trigonometry |
| sqrt, pow, exp, log | Powers and logarithms |
| length, distance, normalize | Vector operations |
| dot, cross | Vector products |
| set | Construct vector/matrix |
| getcomp, setcomp | Component access |

## Noise Functions

| Function | Range | Description |
|----------|-------|-------------|
| noise | 0-1 | Perlin noise |
| snoise | -1 to 1 | Signed Perlin |
| onoise | varies | Original noise |
| curlnoise | vector | Divergence-free |
| random | 0-1 | Deterministic random |
| rand | 0-1 | Random (deprecated) |

---

# APPENDIX B: TOPIC BUCKET DEFINITIONS

## point_cloud_ops
Operations involving spatial queries and point cloud processing.

**Key indicators:** pcopen, pcfind, nearpoints, relaxation, neighbor, clustering, spatial hash

**Example tasks:** Point relaxation, neighbor averaging, clustering, spatial queries, KD-tree operations

## optimization_patterns
Techniques for improving VEX performance.

**Key indicators:** caching, precompute, bounds check, early exit, vectorized, batch

**Example tasks:** Reducing complexity, caching expensive computations, minimizing pcopen calls, loop optimization

## field_analysis
Working with scalar and vector fields.

**Key indicators:** gradient, sample, field, density, ascent, descent, isocontour

**Example tasks:** Gradient computation, field sampling, isosurface extraction, advection

## flow_visualization
Creating flow-based visualizations.

**Key indicators:** advect, curl, streamline, flow, velocity, vector field

**Example tasks:** Particle advection, curl noise trails, streamline generation, flow field visualization

## edge_topology
Mesh connectivity and topology operations.

**Key indicators:** neighbour, hedge, edge, connectivity, traverse, boundary

**Example tasks:** Edge detection, mesh traversal, boundary extraction, topology analysis

## subdivision_surfaces
Subdivision surface operations.

**Key indicators:** subdivide, limit surface, smooth, interpolate, crease

**Example tasks:** Limit surface sampling, subdivision patterns, smooth interpolation

## attribute_operations
General attribute manipulation.

**Key indicators:** transfer, blend, promote, group, attrib, convert

**Example tasks:** Attribute transfer, blending, type conversion, grouping

## debugging_patterns
Error handling and debugging techniques.

**Key indicators:** validate, check, error, assert, debug, print, test

**Example tasks:** Error checking, validation, debugging output, testing patterns

---

*End of Blueprint v1.0*
