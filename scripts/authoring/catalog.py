"""Curated, hand-authored VEX samples for Houdini 21.0.630+.

These are original works, licensed MIT (see ``LICENSE``), authored to fill the
gap identified in ``docs/SAMPLE_STRATEGY.md``: the modern Houdini feature set
(MPM, APEX, Solaris, look dev, etc.) has almost no redistributable open-source
VEX corpus, so we author and verify our own.

Authoring contract for every sample:
  - The VEX is idiomatic and intended to compile/cook on Houdini 21.0.630.
    It is NOT marked ``verified`` until it has actually passed the cook gate
    (``scripts/quality/verify_vex.py``) on a real Houdini install -- see the
    honesty requirement in the strategy doc.
  - Keep snippets small and single-concept, scaled by ``difficulty``.
  - ``vex_context`` is the execution context; ``subcontext`` names the node;
    ``domain`` is the workflow area (Domain enum).

To regenerate the JSONL under ``data/authored/``:
    python scripts/authoring/build_authored.py
"""

from __future__ import annotations

# Each entry is a plain dict. License/source/verification metadata is stamped
# at ingest time by scripts/import_authored.py, not here.

PROCEDURAL_MODELING = [
    {
        "id": "auth_proc_001",
        "title": "Align Copies to Surface Normal",
        "subcontext": "point_wrangle",
        "difficulty": "intermediate",
        "content_type": "pattern",
        "prompt": "Orient instances so they stand up along each point's surface normal",
        "alternative_prompts": [
            "align copy to points to the normal",
            "build an orient quaternion from N",
            "make scattered objects follow the surface",
        ],
        "code": (
            "// Point Wrangle: build an orient quaternion that rotates a\n"
            "// reference up-vector onto each point's surface normal. Copy to\n"
            "// Points reads p@orient automatically.\n"
            "vector up = chv(\"up\");            // e.g. {0, 1, 0}\n"
            "vector n  = normalize(@N);\n"
            "p@orient  = dihedral(up, n);"
        ),
        "explanation": (
            "dihedral() returns the quaternion that rotates the first vector "
            "onto the second, so this aligns each instance's local up-axis to "
            "the surface normal. Copy to Points (and the USD Instancer) read "
            "p@orient directly, making this the canonical way to plant objects "
            "on a surface."
        ),
        "functions_referenced": ["chv", "normalize", "dihedral"],
        "attributes_read": ["N"],
        "attributes_written": ["orient"],
    },
    {
        "id": "auth_proc_002",
        "title": "Scale Points by Neighbour Density",
        "subcontext": "point_wrangle",
        "difficulty": "intermediate",
        "content_type": "pattern",
        "prompt": "Make points smaller where the point cloud is denser",
        "alternative_prompts": [
            "pscale from point cloud density",
            "count neighbours with pcopen",
            "vary size by local crowding",
        ],
        "code": (
            "// Point Wrangle: smaller pscale where neighbours are dense.\n"
            "int h = pcopen(0, \"P\", @P, chf(\"radius\"), chi(\"maxpts\"));\n"
            "int n = pcnumfound(h);\n"
            "@pscale = fit(n, 1, chi(\"maxpts\"), chf(\"maxsize\"), chf(\"minsize\"));\n"
            "pcclose(h);"
        ),
        "explanation": (
            "pcopen() opens a point cloud query around @P within a radius and "
            "pcnumfound() returns how many points it found. fit() inverts that "
            "count into a size, so crowded regions get small points and sparse "
            "regions get large ones. Always pcclose() the handle."
        ),
        "functions_referenced": ["pcopen", "pcnumfound", "fit", "pcclose", "chf", "chi"],
        "attributes_read": ["P"],
        "attributes_written": ["pscale"],
    },
    {
        "id": "auth_proc_003",
        "title": "Random Colour per Connected Piece",
        "subcontext": "point_wrangle",
        "difficulty": "beginner",
        "content_type": "pattern",
        "prompt": "Give every connected piece a unique random colour",
        "alternative_prompts": [
            "random colour from class attribute",
            "colour connectivity pieces",
            "rand on i@class",
        ],
        "code": (
            "// Point/Prim Wrangle downstream of a Connectivity SOP that\n"
            "// writes i@class. Each piece gets a stable random colour.\n"
            "@Cd = rand(i@class * 0.731 + 12.3);"
        ),
        "explanation": (
            "A Connectivity SOP labels each disconnected piece with an integer "
            "i@class. Feeding that integer (scaled to decorrelate it) into "
            "rand() produces a deterministic random colour per piece -- the "
            "standard way to visualise fracture or scatter groupings."
        ),
        "functions_referenced": ["rand"],
        "attributes_read": ["class"],
        "attributes_written": ["Cd"],
    },
    {
        "id": "auth_proc_004",
        "title": "Connect Points into a Polyline",
        "subcontext": "detail_wrangle",
        "difficulty": "intermediate",
        "content_type": "pattern",
        "prompt": "Build a single open polyline through all input points",
        "alternative_prompts": [
            "addprim addvertex polyline",
            "create a curve from points in VEX",
            "stitch points in order",
        ],
        "code": (
            "// Detail Wrangle: connect every input point, in order, into one\n"
            "// open polyline.\n"
            "int n    = npoints(0);\n"
            "int prim = addprim(0, \"polyline\");\n"
            "for (int i = 0; i < n; i++)\n"
            "    addvertex(0, prim, i);"
        ),
        "explanation": (
            "Running in detail (once) mode, this creates one polyline primitive "
            "and appends a vertex referencing each point number in turn. It is "
            "the VEX equivalent of an Add SOP's 'by group' polyline mode, but "
            "fully procedural and reorderable."
        ),
        "functions_referenced": ["npoints", "addprim", "addvertex"],
        "attributes_read": [],
        "attributes_written": [],
    },
    {
        "id": "auth_proc_005",
        "title": "Ridged fBm Displacement along Normal",
        "subcontext": "point_wrangle",
        "difficulty": "advanced",
        "content_type": "pattern",
        "prompt": "Displace a surface along its normal with layered ridged noise",
        "alternative_prompts": [
            "fbm fractal displacement",
            "octave noise loop along N",
            "ridged terrain from noise",
        ],
        "code": (
            "// Point Wrangle: accumulate octaves of absolute noise (ridged\n"
            "// fBm) and push each point along its normal.\n"
            "float amp  = chf(\"amp\");\n"
            "float freq = chf(\"freq\");\n"
            "float n = 0;\n"
            "for (int o = 0; o < 4; o++) {\n"
            "    float g = pow(2.0, o);\n"
            "    n += abs(noise(@P * freq * g)) / g;\n"
            "}\n"
            "@P += normalize(@N) * n * amp;"
        ),
        "explanation": (
            "Each octave doubles frequency and halves amplitude; taking abs() "
            "of the noise creates sharp ridges instead of smooth hills. The "
            "summed value drives displacement along the normalised normal, a "
            "classic procedural-terrain pattern."
        ),
        "functions_referenced": ["chf", "pow", "abs", "noise", "normalize"],
        "attributes_read": ["P", "N"],
        "attributes_written": ["P"],
    },
    {
        "id": "auth_proc_006",
        "title": "Cull Points Beyond a Radius",
        "subcontext": "point_wrangle",
        "difficulty": "beginner",
        "content_type": "pattern",
        "prompt": "Delete points farther than a given distance from the origin",
        "alternative_prompts": [
            "removepoint by distance",
            "cull points outside radius",
            "spherical clip in VEX",
        ],
        "code": (
            "// Point Wrangle: remove any point outside a spherical radius.\n"
            "if (length(@P) > chf(\"radius\"))\n"
            "    removepoint(0, @ptnum);"
        ),
        "explanation": (
            "length(@P) is the distance from the origin; points beyond the "
            "radius are deleted with removepoint(). Because removepoint runs "
            "safely per-point in a wrangle, this is a cheap procedural clip "
            "with no group bookkeeping."
        ),
        "functions_referenced": ["length", "chf", "removepoint"],
        "attributes_read": ["P"],
        "attributes_written": [],
    },
    {
        "id": "auth_proc_007",
        "title": "Curvature Mask from Neighbour Normals",
        "subcontext": "point_wrangle",
        "difficulty": "advanced",
        "content_type": "pattern",
        "prompt": "Estimate surface curvature by comparing a point's normal to its neighbours",
        "alternative_prompts": [
            "convexity mask from normals",
            "neighbours() average normal",
            "edge wear curvature attribute",
        ],
        "code": (
            "// Point Wrangle (after a Normal SOP): higher @curve where the\n"
            "// surface bends away from its neighbours.\n"
            "int nb[] = neighbours(0, @ptnum);\n"
            "vector navg = {0, 0, 0};\n"
            "foreach (int pt; nb)\n"
            "    navg += point(0, \"N\", pt);\n"
            "navg = normalize(navg / max(len(nb), 1));\n"
            "f@curve = 1.0 - dot(navg, normalize(@N));"
        ),
        "explanation": (
            "neighbours() returns the connected point indices; averaging their "
            "normals gives the local mean orientation. The dot product between "
            "that average and this point's normal is ~1 on flat areas and drops "
            "on curved areas, so 1 - dot is a handy curvature/edge-wear mask."
        ),
        "functions_referenced": ["neighbours", "point", "normalize", "max", "len", "dot"],
        "attributes_read": ["N"],
        "attributes_written": ["curve"],
    },
    {
        "id": "auth_proc_008",
        "title": "Arc-Length Parameter along a Polyline",
        "subcontext": "primitive_wrangle",
        "difficulty": "advanced",
        "content_type": "pattern",
        "prompt": "Write a normalized arc-length u value along each polyline's points",
        "alternative_prompts": [
            "curveu by arc length",
            "parametrise a curve in VEX",
            "running distance along a polyline",
        ],
        "code": (
            "// Primitive Wrangle: write point attribute curveu in [0,1] by\n"
            "// accumulated arc length along each polyline.\n"
            "int pts[] = primpoints(0, @primnum);\n"
            "float total = 0;\n"
            "for (int i = 1; i < len(pts); i++)\n"
            "    total += distance(point(0, \"P\", pts[i-1]), point(0, \"P\", pts[i]));\n"
            "float run = 0;\n"
            "setpointattrib(0, \"curveu\", pts[0], 0.0);\n"
            "for (int i = 1; i < len(pts); i++) {\n"
            "    run += distance(point(0, \"P\", pts[i-1]), point(0, \"P\", pts[i]));\n"
            "    setpointattrib(0, \"curveu\", pts[i], total > 0 ? run / total : 0.0);\n"
            "}"
        ),
        "explanation": (
            "primpoints() gives the ordered points of each primitive. A first "
            "pass sums total length; a second pass writes the running fraction "
            "as curveu via setpointattrib(). Unlike the Resample SOP's curveu, "
            "this works without resampling and preserves the original points."
        ),
        "functions_referenced": ["primpoints", "len", "distance", "point", "setpointattrib"],
        "attributes_read": ["P"],
        "attributes_written": ["curveu"],
    },
    {
        "id": "auth_proc_009",
        "title": "Per-Copy Variation for Copy to Points",
        "subcontext": "point_wrangle",
        "difficulty": "intermediate",
        "content_type": "pattern",
        "prompt": "Randomize orientation, scale and colour per instance for Copy to Points",
        "alternative_prompts": [
            "random orient pscale Cd per point",
            "instance variation wrangle",
            "scatter variation seed by ptnum",
        ],
        "code": (
            "// Point Wrangle: independent random orient, size and colour per\n"
            "// copy. Offsetting the seed decorrelates each attribute.\n"
            "float seed = @ptnum;\n"
            "p@orient = quaternion(rand(seed) * 2 * PI, {0, 1, 0});\n"
            "@pscale  = fit01(rand(seed + 1.7), chf(\"smin\"), chf(\"smax\"));\n"
            "@Cd      = set(rand(seed + 3.1), rand(seed + 5.2), rand(seed + 7.3));"
        ),
        "explanation": (
            "Each call to rand() with a distinct seed offset yields an "
            "independent stream, so orientation, scale and colour vary without "
            "correlating. quaternion(angle, axis) spins each copy about Y; "
            "fit01 maps the unit random into a size range."
        ),
        "functions_referenced": ["quaternion", "rand", "fit01", "set", "chf"],
        "attributes_read": [],
        "attributes_written": ["orient", "pscale", "Cd"],
    },
    {
        "id": "auth_proc_010",
        "title": "Snap Positions to a Grid",
        "subcontext": "point_wrangle",
        "difficulty": "beginner",
        "content_type": "pattern",
        "prompt": "Quantize point positions onto a regular grid for welding",
        "alternative_prompts": [
            "round positions to grid",
            "voxel snap points",
            "quantize P before fuse",
        ],
        "code": (
            "// Point Wrangle: snap each position to the nearest grid cell so a\n"
            "// following Fuse SOP welds coincident points cleanly.\n"
            "float g = chf(\"grid\");\n"
            "@P = round(@P / g) * g;"
        ),
        "explanation": (
            "Dividing by the cell size, rounding, and multiplying back snaps "
            "positions to a lattice. This is the standard pre-pass before a "
            "Fuse to merge near-coincident geometry onto exact shared points."
        ),
        "functions_referenced": ["chf", "round"],
        "attributes_read": ["P"],
        "attributes_written": ["P"],
    },
]


MPM = [
    {
        "id": "auth_mpm_001",
        "title": "Launch Velocity on MPM Source",
        "subcontext": "mpm_source_wrangle",
        "vex_context": ["sop"],
        "difficulty": "beginner",
        "content_type": "pattern",
        "houdini_version_notes": "MPM Solver is new in Houdini 21.0; set v@v on the source before the solver.",
        "prompt": "Give MPM source material an initial throwing velocity",
        "alternative_prompts": [
            "set initial velocity for MPM",
            "throw MPM chunk with v@v",
            "launch material point particles",
        ],
        "code": (
            "// Point Wrangle on the MPM source geometry (before the MPM\n"
            "// Solver SOP). v@v becomes the particles' initial velocity.\n"
            "v@v = chv(\"launch_velocity\");   // e.g. {3, 2, 0}"
        ),
        "explanation": (
            "The MPM Solver seeds particle velocity from v@v on the incoming "
            "source geometry. Authoring it in a wrangle (rather than a constant) "
            "lets you vary the throw spatially -- e.g. by position or a paint "
            "mask -- before simulation starts."
        ),
        "functions_referenced": ["chv"],
        "attributes_read": [],
        "attributes_written": ["v"],
    },
    {
        "id": "auth_mpm_002",
        "title": "Colour MPM Particles by Speed",
        "subcontext": "point_wrangle",
        "vex_context": ["sop"],
        "difficulty": "beginner",
        "content_type": "pattern",
        "houdini_version_notes": "Post-sim visualisation; v@v is written by the MPM Solver.",
        "prompt": "Visualize MPM particle speed with a colour ramp",
        "alternative_prompts": [
            "speed ramp on simulated particles",
            "colour by velocity magnitude",
            "chramp from length of v",
        ],
        "code": (
            "// Point Wrangle after the MPM Solver: map speed to a ramp.\n"
            "float spd = length(v@v);\n"
            "@Cd = chramp(\"speed_ramp\", fit(spd, 0, chf(\"max_speed\"), 0, 1));"
        ),
        "explanation": (
            "length(v@v) is the per-particle speed; fit() normalises it against "
            "an expected maximum, and chramp() turns that into a colour. This is "
            "the fastest way to read sim energy at a glance and to tune solver "
            "settings."
        ),
        "functions_referenced": ["length", "chramp", "fit", "chf"],
        "attributes_read": ["v"],
        "attributes_written": ["Cd"],
    },
    {
        "id": "auth_mpm_003",
        "title": "Colour MPM Particles by Elastic Strain",
        "subcontext": "point_wrangle",
        "vex_context": ["sop"],
        "difficulty": "intermediate",
        "content_type": "pattern",
        "houdini_version_notes": "f@Je (elastic volume ratio) is written per particle by the MPM Solver in Houdini 21; confirm the attribute name for your build.",
        "prompt": "Show compression and stretch in an MPM sim using the elastic Jacobian",
        "alternative_prompts": [
            "visualise Je elastic strain",
            "colour MPM by deformation",
            "compression stretch heatmap",
        ],
        "code": (
            "// Point Wrangle after the MPM Solver. @Je is the elastic volume\n"
            "// ratio: <1 compressed, >1 stretched. Map distance from 1.\n"
            "float strain = abs(f@Je - 1.0);\n"
            "@Cd = chramp(\"strain_ramp\", fit(strain, 0, chf(\"max_strain\"), 0, 1));"
        ),
        "explanation": (
            "The MPM solver tracks the elastic part of the deformation gradient; "
            "its determinant Je measures local volume change. abs(Je - 1) is a "
            "symmetric strain magnitude that highlights where the material is "
            "compressed or stretched -- useful for art-directing yield/fracture."
        ),
        "functions_referenced": ["abs", "chramp", "fit", "chf"],
        "attributes_read": ["Je"],
        "attributes_written": ["Cd"],
    },
    {
        "id": "auth_mpm_004",
        "title": "Custom Radial Force in an MPM Solver",
        "subcontext": "solver_geometry_wrangle",
        "vex_context": ["solver"],
        "difficulty": "advanced",
        "content_type": "pattern",
        "houdini_version_notes": "Runs inside a solver stepping the MPM points; scale impulses by f@TimeInc to stay substep-independent.",
        "prompt": "Add an outward explosion impulse to MPM particles each substep",
        "alternative_prompts": [
            "radial force on material point particles",
            "explosion impulse in a solver wrangle",
            "nudge v outward from a centre",
        ],
        "code": (
            "// Geometry Wrangle inside a solver stepping the MPM points.\n"
            "// Outward impulse, scaled by the substep time for stability.\n"
            "vector center = chv(\"center\");\n"
            "vector dir = @P - center;\n"
            "float d = max(length(dir), 1e-4);\n"
            "v@v += (dir / d) * chf(\"strength\") * f@TimeInc;"
        ),
        "explanation": (
            "Normalising (P - center) gives an outward direction; multiplying by "
            "strength and f@TimeInc turns it into a per-substep velocity change "
            "that is independent of substep count. Clamping the distance avoids a "
            "divide-by-zero at the exact centre."
        ),
        "functions_referenced": ["chv", "max", "length", "chf"],
        "attributes_read": ["P", "v", "TimeInc"],
        "attributes_written": ["v"],
    },
    {
        "id": "auth_mpm_005",
        "title": "Vortex Force around an Axis",
        "subcontext": "solver_geometry_wrangle",
        "vex_context": ["solver"],
        "difficulty": "advanced",
        "content_type": "pattern",
        "houdini_version_notes": "Solver-stepped MPM points; uses f@TimeInc for substep independence.",
        "prompt": "Swirl MPM particles around a vertical axis",
        "alternative_prompts": [
            "vortex velocity force",
            "tangential swirl in a solver wrangle",
            "cross product spin around axis",
        ],
        "code": (
            "// Geometry Wrangle inside a solver: tangential swirl around an\n"
            "// axis through 'center'.\n"
            "vector axis   = normalize(chv(\"axis\"));\n"
            "vector center = chv(\"center\");\n"
            "vector r = @P - center;\n"
            "vector tangent = cross(axis, r);\n"
            "v@v += normalize(tangent) * chf(\"strength\") * f@TimeInc;"
        ),
        "explanation": (
            "The cross product of the axis and the radial vector points along "
            "the local tangent (the direction of rotation). Normalising and "
            "scaling it by strength and f@TimeInc adds a steady swirl without "
            "blowing particles outward."
        ),
        "functions_referenced": ["normalize", "chv", "cross", "chf"],
        "attributes_read": ["P", "v", "TimeInc"],
        "attributes_written": ["v"],
    },
    {
        "id": "auth_mpm_006",
        "title": "Curl-Noise Turbulence on MPM Velocity",
        "subcontext": "solver_geometry_wrangle",
        "vex_context": ["solver"],
        "difficulty": "advanced",
        "content_type": "pattern",
        "houdini_version_notes": "Solver-stepped MPM points; curlnoise keeps the added field divergence-free.",
        "prompt": "Add divergence-free turbulence to MPM particle velocity",
        "alternative_prompts": [
            "curlnoise turbulence force",
            "evolving noise on velocity",
            "wispy detail for material point sim",
        ],
        "code": (
            "// Geometry Wrangle inside a solver: divergence-free turbulence.\n"
            "vector p = @P * chf(\"freq\") + f@Time * chv(\"evolve\");\n"
            "v@v += curlnoise(p) * chf(\"amp\") * f@TimeInc;"
        ),
        "explanation": (
            "curlnoise() returns a vector field with zero divergence, so it adds "
            "swirling detail without artificially compressing or expanding the "
            "material. Advancing the sample point by f@Time makes the turbulence "
            "evolve over the simulation."
        ),
        "functions_referenced": ["chf", "chv", "curlnoise"],
        "attributes_read": ["P", "v", "Time", "TimeInc"],
        "attributes_written": ["v"],
    },
    {
        "id": "auth_mpm_007",
        "title": "Pin MPM Particles below a Height",
        "subcontext": "solver_geometry_wrangle",
        "vex_context": ["solver"],
        "difficulty": "intermediate",
        "content_type": "pattern",
        "houdini_version_notes": "Solver-stepped MPM points; zeroing v anchors a region (a simple pin/glue).",
        "prompt": "Anchor MPM particles that fall below a floor height",
        "alternative_prompts": [
            "pin particles in a region",
            "freeze MPM velocity below threshold",
            "glue material points in place",
        ],
        "code": (
            "// Geometry Wrangle inside a solver: zero the velocity of any\n"
            "// particle below the floor to pin it.\n"
            "if (@P.y < chf(\"floor\"))\n"
            "    v@v = {0, 0, 0};"
        ),
        "explanation": (
            "Setting v@v to zero each substep holds those particles roughly in "
            "place, an easy way to create anchored or glued regions of a soft "
            "body without a separate constraint network. Use a smooth mask "
            "instead of a hard cutoff to avoid a visible seam."
        ),
        "functions_referenced": ["chf"],
        "attributes_read": ["P", "v"],
        "attributes_written": ["v"],
    },
    {
        "id": "auth_mpm_008",
        "title": "MPM Particle Radius from Density",
        "subcontext": "mpm_source_wrangle",
        "vex_context": ["sop"],
        "difficulty": "intermediate",
        "content_type": "pattern",
        "houdini_version_notes": "Set on the MPM source before the solver; drives per-particle pscale.",
        "prompt": "Vary MPM particle size from an input density attribute",
        "alternative_prompts": [
            "pscale from density for MPM",
            "remap density to radius",
            "variable particle size source",
        ],
        "code": (
            "// Point Wrangle on the MPM source: map an input f@density into a\n"
            "// particle radius range.\n"
            "@pscale = fit(f@density, chf(\"dmin\"), chf(\"dmax\"),\n"
            "              chf(\"rmin\"), chf(\"rmax\"));"
        ),
        "explanation": (
            "fit() remaps the incoming density into a sensible radius band so "
            "denser regions sample with larger or smaller particles as desired. "
            "Controlling pscale at the source is the cleanest way to balance "
            "detail against solve cost."
        ),
        "functions_referenced": ["fit", "chf"],
        "attributes_read": ["density"],
        "attributes_written": ["pscale"],
    },
]


LOOK_DEVELOPMENT = [
    {
        "id": "auth_look_001",
        "title": "Fresnel Rim in a Material Snippet",
        "subcontext": "snippet_vop",
        "vex_context": ["material"],
        "difficulty": "intermediate",
        "content_type": "pattern",
        "prompt": "Add a fresnel rim light term to a surface shader",
        "alternative_prompts": [
            "fresnel rim shading",
            "view-dependent edge glow",
            "pow of one minus dot N V",
        ],
        "code": (
            "// Snippet VOP in a material network. I is the incident ray, so\n"
            "// -I points toward the camera. Add a rim that grows at grazing\n"
            "// angles.\n"
            "vector nN = normalize(N);\n"
            "vector v  = normalize(-I);\n"
            "float f = pow(1.0 - clamp(dot(nN, v), 0, 1), chf(\"rim_power\"));\n"
            "Cf += f * chv(\"rim_color\");"
        ),
        "explanation": (
            "The fresnel falloff 1 - dot(N, V) is near zero facing the camera "
            "and approaches one at the silhouette; raising it to a power tightens "
            "the rim. Adding the result to Cf gives a cheap, art-directable edge "
            "light entirely in VEX."
        ),
        "functions_referenced": ["normalize", "pow", "clamp", "dot", "chf", "chv"],
        "attributes_read": ["N", "I"],
        "attributes_written": ["Cf"],
    },
    {
        "id": "auth_look_002",
        "title": "World-Space Noise Tint",
        "subcontext": "snippet_vop",
        "vex_context": ["material"],
        "difficulty": "beginner",
        "content_type": "pattern",
        "prompt": "Blend two colours with low-frequency world-space noise",
        "alternative_prompts": [
            "procedural colour variation shader",
            "noise tint between two colours",
            "lerp colours by noise",
        ],
        "code": (
            "// Snippet VOP: tint the surface using world-space noise so the\n"
            "// variation stays put as geometry deforms.\n"
            "float n = noise(P * chf(\"scale\"));\n"
            "Cf = lerp(chv(\"color_a\"), chv(\"color_b\"), n);"
        ),
        "explanation": (
            "Sampling noise at the shading position P gives a smooth 0-1 field "
            "that lerp() uses to mix two colours. Using world-space P keeps the "
            "pattern anchored to the object even under animation."
        ),
        "functions_referenced": ["noise", "chf", "lerp", "chv"],
        "attributes_read": ["P"],
        "attributes_written": ["Cf"],
    },
    {
        "id": "auth_look_003",
        "title": "Vertical Gradient Blend",
        "subcontext": "snippet_vop",
        "vex_context": ["material"],
        "difficulty": "beginner",
        "content_type": "pattern",
        "prompt": "Blend two colours by height for a simple gradient material",
        "alternative_prompts": [
            "height gradient shader",
            "fit P.y to colour ramp",
            "top to bottom colour blend",
        ],
        "code": (
            "// Snippet VOP: vertical colour gradient between two bounds.\n"
            "float h = fit(P.y, chf(\"low\"), chf(\"high\"), 0, 1);\n"
            "Cf = lerp(chv(\"bottom\"), chv(\"top\"), clamp(h, 0, 1));"
        ),
        "explanation": (
            "fit() remaps world height into 0-1 between the chosen bounds and "
            "clamp() keeps it in range past the limits. The result drives a "
            "vertical colour blend -- a building block for stratified rock, "
            "gradient backdrops and sky-style tints."
        ),
        "functions_referenced": ["fit", "chf", "lerp", "chv", "clamp"],
        "attributes_read": ["P"],
        "attributes_written": ["Cf"],
    },
    {
        "id": "auth_look_004",
        "title": "Procedural Noise Displacement",
        "subcontext": "displacement_snippet",
        "vex_context": ["material"],
        "difficulty": "intermediate",
        "content_type": "pattern",
        "houdini_version_notes": "Recompute shading normals downstream (Compute Normal VOP or the Displace node's option).",
        "prompt": "Displace a surface along its normal in a displacement shader",
        "alternative_prompts": [
            "displacement shader noise",
            "offset P along N in material",
            "procedural bump displacement",
        ],
        "code": (
            "// Displacement Snippet VOP: signed noise offset along the normal.\n"
            "// Refit shading normals afterward (Compute Normal VOP / Displace\n"
            "// node option) so lighting follows the new surface.\n"
            "float d = (noise(P * chf(\"freq\")) - 0.5) * 2.0 * chf(\"amp\");\n"
            "P += normalize(N) * d;"
        ),
        "explanation": (
            "Centring the noise around zero (-0.5..0.5, scaled) gives a signed "
            "displacement so the surface pushes both in and out. Moving P along "
            "the normal is true geometric displacement; remember to recompute N "
            "or the shading will still reflect the original surface."
        ),
        "functions_referenced": ["noise", "chf", "normalize"],
        "attributes_read": ["P", "N"],
        "attributes_written": ["P"],
    },
    {
        "id": "auth_look_005",
        "title": "Standalone CVEX Surface Shader",
        "subcontext": "cvex",
        "vex_context": ["cvex", "material"],
        "difficulty": "advanced",
        "content_type": "pattern",
        "houdini_version_notes": "Compile with vcc and bind as a material, or paste into an Inline VOP.",
        "prompt": "Write a minimal standalone CVEX surface shader with a lambert term",
        "alternative_prompts": [
            "cvex shader signature example",
            "export Cf lambert shader",
            "standalone VEX material",
        ],
        "code": (
            "// Standalone CVEX surface shader. Compile with vcc:\n"
            "//   vcc simple_surface.vfl\n"
            "cvex simple_surface(\n"
            "        export vector Cf = {0, 0, 0};\n"
            "        vector P = {0, 0, 0};\n"
            "        vector N = {0, 1, 0};\n"
            "        vector baseColor = {0.6, 0.6, 0.6})\n"
            "{\n"
            "    vector nN  = normalize(N);\n"
            "    vector L   = normalize({0.4, 1.0, 0.3});\n"
            "    float  ndl = max(dot(nN, L), 0);\n"
            "    Cf = baseColor * (0.2 + 0.8 * ndl);\n"
            "}"
        ),
        "explanation": (
            "A cvex function declares its bound parameters in the signature; "
            "'export' marks outputs the shading network reads back (here Cf). "
            "The body is an ambient-plus-lambert term against a fixed light "
            "direction -- the smallest complete shader you can compile with vcc."
        ),
        "functions_referenced": ["normalize", "max", "dot"],
        "attributes_read": ["P", "N"],
        "attributes_written": ["Cf"],
    },
    {
        "id": "auth_look_006",
        "title": "Procedural UV Checker",
        "subcontext": "snippet_vop",
        "vex_context": ["material"],
        "difficulty": "intermediate",
        "content_type": "pattern",
        "prompt": "Generate a checkerboard pattern from UV coordinates in a shader",
        "alternative_prompts": [
            "checker pattern from s t",
            "uv checkerboard shader",
            "modulo tiles colour",
        ],
        "code": (
            "// Snippet VOP: a checkerboard from the surface UVs (s, t).\n"
            "int cx = (int)floor(s * chf(\"tiles\"));\n"
            "int cy = (int)floor(t * chf(\"tiles\"));\n"
            "float c = (cx + cy) % 2;\n"
            "Cf = lerp(chv(\"color_a\"), chv(\"color_b\"), c);"
        ),
        "explanation": (
            "Flooring the scaled UVs gives integer tile indices; the parity of "
            "their sum alternates 0/1 across the grid, producing a checker. It is "
            "the canonical UV-debug pattern and a base for tiled procedural "
            "textures."
        ),
        "functions_referenced": ["floor", "chf", "lerp", "chv"],
        "attributes_read": ["s", "t"],
        "attributes_written": ["Cf"],
    },
]


APEX = [
    {
        "id": "auth_apex_001",
        "title": "Blend Two Vectors (RunVex)",
        "subcontext": "runvex",
        "vex_context": ["apex"],
        "difficulty": "beginner",
        "content_type": "pattern",
        "houdini_version_notes": "APEX RunVex uses named inputs/outputs and has NO @ attribute syntax.",
        "prompt": "Linearly blend two vectors by a factor inside an APEX RunVex node",
        "alternative_prompts": [
            "apex lerp node in vex",
            "named input output blend",
            "interpolate vectors in apex",
        ],
        "code": (
            "// APEX RunVex. Inputs: vector a, b; float t. Output: vector result.\n"
            "// No '@' syntax in APEX VEX -- operate on the named ports directly.\n"
            "result = lerp(a, b, t);"
        ),
        "explanation": (
            "APEX RunVex behaves like a Snippet VOP but binds named inputs and "
            "outputs instead of geometry attributes, so there is no '@' syntax. "
            "Here it simply interpolates between two vector inputs by t."
        ),
        "functions_referenced": ["lerp"],
        "attributes_read": [],
        "attributes_written": [],
    },
    {
        "id": "auth_apex_002",
        "title": "Clamp a Control Value (RunVex)",
        "subcontext": "runvex",
        "vex_context": ["apex"],
        "difficulty": "beginner",
        "content_type": "pattern",
        "houdini_version_notes": "Named inputs/outputs; no @ syntax.",
        "prompt": "Clamp a rig control value into a range in APEX",
        "alternative_prompts": [
            "apex clamp value",
            "limit a control in runvex",
            "constrain float between bounds",
        ],
        "code": (
            "// APEX RunVex. Inputs: float value, lo, hi. Output: float result.\n"
            "result = clamp(value, lo, hi);"
        ),
        "explanation": (
            "A minimal RunVex clamp, useful for bounding a driven rig control "
            "(e.g. a corrective blendshape weight) so animators cannot push it "
            "out of its valid range."
        ),
        "functions_referenced": ["clamp"],
        "attributes_read": [],
        "attributes_written": [],
    },
    {
        "id": "auth_apex_003",
        "title": "Smoothstep Easing (RunVex)",
        "subcontext": "runvex",
        "vex_context": ["apex"],
        "difficulty": "beginner",
        "content_type": "pattern",
        "houdini_version_notes": "Named inputs/outputs; no @ syntax.",
        "prompt": "Ease a 0-1 control with a smoothstep curve in APEX",
        "alternative_prompts": [
            "apex smoothstep easing",
            "ease in out runvex",
            "soft ramp control",
        ],
        "code": (
            "// APEX RunVex. Input: float t in [0,1]. Output: float result.\n"
            "result = smooth(0.0, 1.0, t);"
        ),
        "explanation": (
            "smooth() is VEX's smoothstep: it eases a value with zero slope at "
            "both ends. In a rig it softens the response of a driven control so "
            "motion starts and stops gently."
        ),
        "functions_referenced": ["smooth"],
        "attributes_read": [],
        "attributes_written": [],
    },
    {
        "id": "auth_apex_004",
        "title": "Compose a Transform Matrix (RunVex)",
        "subcontext": "runvex",
        "vex_context": ["apex"],
        "difficulty": "intermediate",
        "content_type": "pattern",
        "houdini_version_notes": "Named inputs/outputs; no @ syntax. translate()/scale() modify the matrix in place.",
        "prompt": "Build a 4x4 transform from a translation and uniform scale in APEX",
        "alternative_prompts": [
            "apex build matrix from trs",
            "compose transform runvex",
            "ident scale translate matrix",
        ],
        "code": (
            "// APEX RunVex. Inputs: vector t; float s. Output: matrix xform.\n"
            "matrix m = ident();\n"
            "scale(m, set(s, s, s));\n"
            "translate(m, t);\n"
            "xform = m;"
        ),
        "explanation": (
            "Starting from the identity, scale() then translate() mutate the "
            "matrix in place to build a TRS transform. Emitting it as a named "
            "matrix output lets downstream APEX nodes apply it to a joint or "
            "control."
        ),
        "functions_referenced": ["ident", "scale", "set", "translate"],
        "attributes_read": [],
        "attributes_written": [],
    },
    {
        "id": "auth_apex_005",
        "title": "Remap a Control Range (RunVex)",
        "subcontext": "runvex",
        "vex_context": ["apex"],
        "difficulty": "beginner",
        "content_type": "pattern",
        "houdini_version_notes": "Named inputs/outputs; no @ syntax.",
        "prompt": "Remap a rig control from one range to another in APEX",
        "alternative_prompts": [
            "apex fit value range",
            "remap driver to driven",
            "rescale control runvex",
        ],
        "code": (
            "// APEX RunVex. Inputs: float value, inmin, inmax, outmin, outmax.\n"
            "// Output: float out.\n"
            "out = fit(value, inmin, inmax, outmin, outmax);"
        ),
        "explanation": (
            "fit() linearly remaps a value between input and output ranges -- the "
            "workhorse of driven-key style rigging, e.g. turning a slider's "
            "0-10 range into a joint's -45..45 degrees."
        ),
        "functions_referenced": ["fit"],
        "attributes_read": [],
        "attributes_written": [],
    },
    {
        "id": "auth_apex_006",
        "title": "Euler to Quaternion (RunVex)",
        "subcontext": "runvex",
        "vex_context": ["apex"],
        "difficulty": "intermediate",
        "content_type": "pattern",
        "houdini_version_notes": "Named inputs/outputs; no @ syntax. Euler angles in radians, XYZ order.",
        "prompt": "Convert euler angles to a quaternion in APEX",
        "alternative_prompts": [
            "apex euler to quaternion",
            "eulertoquaternion runvex",
            "rotation order conversion",
        ],
        "code": (
            "// APEX RunVex. Input: vector euler (radians). Output: vector4 q.\n"
            "q = eulertoquaternion(euler, 0);   // 0 = XYZ rotation order"
        ),
        "explanation": (
            "eulertoquaternion() converts per-axis euler angles into a single "
            "quaternion, which is the rotation form most APEX joint operations "
            "expect. The second argument selects the rotation order."
        ),
        "functions_referenced": ["eulertoquaternion"],
        "attributes_read": [],
        "attributes_written": [],
    },
]


SOLARIS = [
    {
        "id": "auth_solaris_001",
        "title": "Point-Instancer Transform Attributes for USD",
        "subcontext": "usd_attr_prep",
        "vex_context": ["sop"],
        "difficulty": "intermediate",
        "content_type": "pattern",
        "houdini_version_notes": "SOP-side prep feeding a Solaris Instancer LOP; USD point instancing reads orient, pscale and an index.",
        "prompt": "Author per-point orient, scale and variant index for a USD instancer",
        "alternative_prompts": [
            "usd instancer attributes in vex",
            "orient pscale variant for solaris",
            "prepare points for point instancing",
        ],
        "code": (
            "// Point Wrangle feeding a Solaris Instancer (via SOP Import).\n"
            "// USD point instancing reads orient (vector4), pscale and an\n"
            "// integer index used to pick a prototype.\n"
            "p@orient  = quaternion(rand(@ptnum) * 2 * PI, {0, 1, 0});\n"
            "@pscale   = fit01(rand(@ptnum + 4.6), 0.8, 1.2);\n"
            "i@variant = int(rand(@ptnum + 9.1) * chi(\"num_variants\"));"
        ),
        "explanation": (
            "The USD point instancer needs an orientation, a scale and a "
            "prototype index per point. Authoring them on the SOP side before "
            "importing to Solaris is the most controllable path -- you get full "
            "VEX expressivity, then LOPs just consume the attributes."
        ),
        "functions_referenced": ["quaternion", "rand", "fit01", "int", "chi"],
        "attributes_read": [],
        "attributes_written": ["orient", "pscale", "variant"],
    },
    {
        "id": "auth_solaris_002",
        "title": "Author USD displayColor via Cd",
        "subcontext": "usd_attr_prep",
        "vex_context": ["sop"],
        "difficulty": "beginner",
        "content_type": "pattern",
        "houdini_version_notes": "Cd on SOP geometry is imported as primvars:displayColor in USD.",
        "prompt": "Set a height-based colour that becomes USD displayColor",
        "alternative_prompts": [
            "cd to usd displaycolor",
            "colour geometry for solaris",
            "primvar displaycolor from vex",
        ],
        "code": (
            "// Point Wrangle before SOP Import. Cd is carried into USD as\n"
            "// primvars:displayColor, so this tints the imported prim.\n"
            "@Cd = chramp(\"tint\", fit(@P.y, chf(\"low\"), chf(\"high\"), 0, 1));"
        ),
        "explanation": (
            "Houdini maps the Cd point attribute to USD's displayColor primvar "
            "on import, so authoring Cd in a wrangle is the simplest way to give "
            "Solaris geometry viewport/preview colour without building a "
            "material."
        ),
        "functions_referenced": ["chramp", "fit", "chf"],
        "attributes_read": ["P"],
        "attributes_written": ["Cd"],
    },
    {
        "id": "auth_solaris_003",
        "title": "Stable USD Prim Names",
        "subcontext": "usd_attr_prep",
        "vex_context": ["sop"],
        "difficulty": "intermediate",
        "content_type": "pattern",
        "houdini_version_notes": "The 'name' / 'path' attribute drives USD prim paths on import.",
        "prompt": "Generate stable USD prim paths from a piece attribute",
        "alternative_prompts": [
            "name attribute for usd paths",
            "sprintf prim path per piece",
            "stable usd hierarchy from vex",
        ],
        "code": (
            "// Primitive Wrangle: build a deterministic USD prim path per\n"
            "// piece so references stay stable across cooks.\n"
            "s@name = sprintf(\"/geo/piece_%d\", i@piece);"
        ),
        "explanation": (
            "USD relies on stable prim paths; deriving s@name from a persistent "
            "piece id (rather than primitive number) keeps paths constant even "
            "when topology order changes, which protects downstream references "
            "and layer overrides."
        ),
        "functions_referenced": ["sprintf"],
        "attributes_read": ["piece"],
        "attributes_written": ["name"],
    },
    {
        "id": "auth_solaris_004",
        "title": "Tag USD Purpose (proxy/render)",
        "subcontext": "usd_attr_prep",
        "vex_context": ["sop"],
        "difficulty": "beginner",
        "content_type": "pattern",
        "houdini_version_notes": "usdpurpose attribute maps to the USD 'purpose' on import.",
        "prompt": "Split geometry into proxy and render purposes for USD",
        "alternative_prompts": [
            "usd purpose attribute vex",
            "proxy vs render tag",
            "set usdpurpose per prim",
        ],
        "code": (
            "// Primitive Wrangle: assign USD purpose so the prim shows as a\n"
            "// lightweight proxy in the viewport but the full mesh at render.\n"
            "s@usdpurpose = (i@is_proxy) ? \"proxy\" : \"render\";"
        ),
        "explanation": (
            "USD's 'purpose' lets a prim carry both a cheap proxy and a heavy "
            "render representation. Authoring usdpurpose in a wrangle on the SOP "
            "side cleanly partitions geometry before it ever reaches Solaris."
        ),
        "functions_referenced": [],
        "attributes_read": ["is_proxy"],
        "attributes_written": ["usdpurpose"],
    },
]


LIGHTING = [
    {
        "id": "auth_light_001",
        "title": "Per-Instance Light Variation",
        "subcontext": "light_instance_attr",
        "vex_context": ["sop"],
        "difficulty": "intermediate",
        "content_type": "pattern",
        "houdini_version_notes": "SOP-side attrs feeding a Solaris light instancer; intensity/Cd become per-light primvars.",
        "prompt": "Randomize intensity and colour across instanced lights",
        "alternative_prompts": [
            "vary instanced light intensity",
            "random warm cool light colour",
            "per light primvar variation",
        ],
        "code": (
            "// Point Wrangle on points that instance lights in Solaris.\n"
            "// These attributes ride along as per-light overrides.\n"
            "@intensity = fit01(rand(@ptnum), chf(\"imin\"), chf(\"imax\"));\n"
            "@Cd = lerp(chv(\"warm\"), chv(\"cool\"), rand(@ptnum + 2.3));"
        ),
        "explanation": (
            "When points drive a light instancer, per-point attributes become "
            "per-light overrides. Randomising intensity and colour breaks up the "
            "uniformity of an array of identical lights -- essential for natural "
            "looking set dressing like string lights or windows."
        ),
        "functions_referenced": ["fit01", "rand", "chf", "lerp", "chv"],
        "attributes_read": [],
        "attributes_written": ["intensity", "Cd"],
    },
    {
        "id": "auth_light_002",
        "title": "Aim Instanced Lights along the Normal",
        "subcontext": "light_instance_attr",
        "vex_context": ["sop"],
        "difficulty": "intermediate",
        "content_type": "pattern",
        "houdini_version_notes": "Spot lights aim down -Z; build orient so -Z follows the surface normal.",
        "prompt": "Orient instanced spotlights to face outward from a surface",
        "alternative_prompts": [
            "aim spotlight along normal",
            "orient lights to surface",
            "dihedral for light direction",
        ],
        "code": (
            "// Point Wrangle: a spot light points down its local -Z, so rotate\n"
            "// -Z onto the surface normal.\n"
            "p@orient = dihedral({0, 0, -1}, normalize(@N));"
        ),
        "explanation": (
            "Houdini/USD spot lights emit along their local -Z axis. dihedral() "
            "builds the quaternion that rotates -Z onto each point's normal, so "
            "instanced lights shine away from the surface they sit on."
        ),
        "functions_referenced": ["dihedral", "normalize"],
        "attributes_read": ["N"],
        "attributes_written": ["orient"],
    },
    {
        "id": "auth_light_003",
        "title": "Distance Falloff for Instanced Lights",
        "subcontext": "light_instance_attr",
        "vex_context": ["sop"],
        "difficulty": "intermediate",
        "content_type": "pattern",
        "houdini_version_notes": "SOP-side intensity attr feeding a light instancer.",
        "prompt": "Fade instanced light intensity with distance from a focal point",
        "alternative_prompts": [
            "exponential light falloff",
            "intensity by distance from target",
            "focus point light dimming",
        ],
        "code": (
            "// Point Wrangle: dim lights farther from a focus point with an\n"
            "// exponential falloff.\n"
            "float d = distance(@P, chv(\"focus\"));\n"
            "@intensity = chf(\"base\") * exp(-d * chf(\"falloff\"));"
        ),
        "explanation": (
            "exp(-d * falloff) gives a smooth exponential dimming with distance, "
            "concentrating brightness near the focus point. Driving the per-light "
            "intensity attribute this way creates a natural pool of light across "
            "an instanced array."
        ),
        "functions_referenced": ["distance", "chv", "chf", "exp"],
        "attributes_read": ["P"],
        "attributes_written": ["intensity"],
    },
]


TOPS = [
    {
        "id": "auth_tops_001",
        "title": "Drive a Wedge from a PDG Detail Attribute",
        "subcontext": "pdg_wedge",
        "vex_context": ["sop"],
        "difficulty": "intermediate",
        "content_type": "pattern",
        "houdini_version_notes": "PDG/TOPs is Python-first; VEX appears only in the SOP cook a work item triggers. A Wedge TOP writes the wedge value onto geometry as a detail attribute.",
        "prompt": "Use a per-work-item wedge value inside a SOP cooked by PDG",
        "alternative_prompts": [
            "read pdg wedge in vex",
            "detail attribute from work item",
            "vary scatter per tops wedge",
        ],
        "code": (
            "// Point Wrangle in a SOP that a TOP graph cooks per work item.\n"
            "// A Wedge TOP exposes its value as a detail attribute 'seed'; use\n"
            "// it so every work item produces a different variant.\n"
            "float seed = detail(0, \"seed\", 0);\n"
            "@P += vector(rand(set(@ptnum, seed))) * chf(\"jitter\");"
        ),
        "explanation": (
            "PDG itself is orchestrated in Python, so the only real VEX surface "
            "is the SOP cook a work item drives. Reading the wedged value from a "
            "detail attribute lets one network emit many deterministic variants, "
            "one per work item."
        ),
        "functions_referenced": ["detail", "vector", "rand", "set", "chf"],
        "attributes_read": ["P"],
        "attributes_written": ["P"],
    },
    {
        "id": "auth_tops_002",
        "title": "Stamp the Work-Item Index onto Geometry",
        "subcontext": "pdg_wedge",
        "vex_context": ["sop"],
        "difficulty": "beginner",
        "content_type": "pattern",
        "houdini_version_notes": "PDG passes the work item index in as a detail attribute or parameter; VEX just records it.",
        "prompt": "Record which PDG work item produced a piece of geometry",
        "alternative_prompts": [
            "tag geometry with work item index",
            "stamp pdg index in vex",
            "identify tops output piece",
        ],
        "code": (
            "// Detail Wrangle in a PDG-cooked SOP. The TOP graph passes the\n"
            "// work item index in as the detail attribute 'pdg_index'; copy it\n"
            "// onto a stable attribute for a later merge to sort on.\n"
            "i@workitem = int(detail(0, \"pdg_index\", 0));"
        ),
        "explanation": (
            "Stamping the work item index onto the geometry lets a downstream "
            "merge or ROP identify and order outputs that came back from many "
            "parallel TOP cooks. It underlines that VEX's role in TOPs is small "
            "and bookkeeping-oriented -- the orchestration lives in Python."
        ),
        "functions_referenced": ["int", "detail"],
        "attributes_read": [],
        "attributes_written": ["workitem"],
    },
]


# All domains keyed by output filename. Defaults applied at build time:
#   vex_context -> ["sop"], houdini_version_min -> "21.0"
CATALOG = {
    "procedural_modeling": ("procedural_modeling", PROCEDURAL_MODELING),
    "mpm": ("mpm", MPM),
    "look_development": ("look_development", LOOK_DEVELOPMENT),
    "apex": ("apex", APEX),
    "solaris": ("solaris", SOLARIS),
    "lighting": ("lighting", LIGHTING),
    "tops": ("tops", TOPS),
}
