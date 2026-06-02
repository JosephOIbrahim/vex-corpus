"""Tests for ingesting the H21 best-practices guide into corpus chunks."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.schema import REDISTRIBUTABLE_LICENSES
from scripts.ingest_best_practices import (
    GUIDE,
    LICENSE,
    _map_section,
    parse_guide,
    record_to_dict,
)

FIXTURE = """# Guide

## 4. Common anti-patterns (before -> after)

**Untyped vector bind**:

```vex
@up = {0, 1, 0};   // BAD
v@up = {0, 1, 0};  // GOOD
```

## 6. Context-specific best practices

### 6.2 Simulation, solvers and MPM (H21)

Add an outward impulse inside the solver.

```vex
v@v += normalize(@P) * chf("s") * f@TimeInc;
```

### 6.6 APEX (rigging)

```vex
result = lerp(a, b, t);
```
"""


def test_map_section_prioritizes_subsection():
    # domain lives in the h3 even though h2 is generic
    domain, ctx, ctype, topic = _map_section("6. Context-specific", "6.2 ... MPM")
    assert domain == "mpm"
    assert topic == "mpm"


def test_map_section_anti_pattern():
    domain, ctx, ctype, topic = _map_section("4. Common anti-patterns", "")
    assert ctype == "troubleshooting"
    assert topic == "best_practices"


def test_parse_fixture_counts_and_routing():
    recs = parse_guide(FIXTURE)
    assert len(recs) == 3

    anti = recs[0]
    assert anti["domain"] == "fundamentals"
    assert anti["content_type"] == "troubleshooting"
    assert anti["llm_topic"] == "best_practices"
    assert anti["title"] == "Untyped vector bind"

    mpm = recs[1]
    assert mpm["domain"] == "mpm"
    assert mpm["vex_context"] == ["solver"]   # f@TimeInc -> solver
    assert mpm["llm_topic"] == "mpm"

    apex = recs[2]
    assert apex["domain"] == "apex"
    assert apex["vex_context"] == ["apex"]
    assert "@" not in apex["code"]            # APEX VEX has no @ syntax


def test_record_to_dict_stamps_metadata():
    rec = parse_guide(FIXTURE)[1]  # the MPM one
    d = record_to_dict(rec, static_only=True)
    assert d["source_id"] == "houdini21-best-practices"
    assert d["license"] == "MIT"
    assert d["llm_topic"] == "mpm"            # carried for Synapse grouping
    assert d["domain"] == "mpm"
    assert d["verification_method"] == "static-lint"
    assert d["verified"] is False
    assert d["houdini_version_min"] == "21.0"
    assert d["code_blocks"][0]["code"]


def test_apex_record_passes_static_lint():
    apex = parse_guide(FIXTURE)[2]
    d = record_to_dict(apex, static_only=True)
    # no @ in APEX code => lint passes => not flagged
    assert d["flagged_for_review"] is False


def test_license_is_redistributable():
    assert LICENSE in REDISTRIBUTABLE_LICENSES


def test_real_guide_ingests_cleanly():
    """Integration: the actual shipped guide parses and routes sensibly."""
    recs = parse_guide(GUIDE.read_text(encoding="utf-8"))
    assert len(recs) >= 15
    topics = {r["llm_topic"] for r in recs}
    # general best practices + at least a couple H21 domains
    assert "best_practices" in topics
    assert {"mpm", "apex"} & topics
    # every record has the fields the corpus needs
    for r in recs:
        assert r["code"].strip()
        assert r["domain"]
        assert r["llm_topic"]
