"""Tests for the v2 schema extensions added for Houdini 21 coverage."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.schema import (
    PIPELINE_VERSION,
    REDISTRIBUTABLE_LICENSES,
    ChunkV2,
    CodeBlock,
    Domain,
    VEXContext,
    migrate_v1_sample,
)


def test_new_contexts_exist():
    assert VEXContext.LOP.value == "lop"
    assert VEXContext.APEX.value == "apex"


def test_domain_enum_covers_requested_areas():
    values = {d.value for d in Domain}
    for expected in {"procedural_modeling", "look_development", "lighting",
                     "apex", "mpm", "solaris", "tops"}:
        assert expected in values


def test_pipeline_version_bumped():
    assert PIPELINE_VERSION == "0.3.0"


def test_redistributable_licenses():
    assert "MIT" in REDISTRIBUTABLE_LICENSES
    assert "Apache-2.0" in REDISTRIBUTABLE_LICENSES
    assert "CC-BY-SA-4.0" in REDISTRIBUTABLE_LICENSES
    # A proprietary / unknown license must NOT be considered redistributable
    assert "proprietary" not in REDISTRIBUTABLE_LICENSES
    assert "" not in REDISTRIBUTABLE_LICENSES


def test_new_fields_roundtrip():
    chunk = ChunkV2(
        id="t1",
        content="explanation",
        code_blocks=[CodeBlock(code="@P.y += 1;")],
        vex_context=["apex"],
        subcontext="runvex",
        domain="apex",
        license="MIT",
        attribution="me",
        houdini_version_min="21.0",
        houdini_version_max="",
        verified=True,
        verification_method="hython-cook",
        verified_houdini_build="21.0.630",
    )
    d = chunk.to_dict()
    # New fields are present in the serialized form
    for key in ("subcontext", "domain", "license", "attribution",
                "houdini_version_max", "verified", "verification_method",
                "verified_houdini_build"):
        assert key in d, f"{key} missing from to_dict()"

    restored = ChunkV2.from_dict(d)
    assert restored.subcontext == "runvex"
    assert restored.domain == "apex"
    assert restored.license == "MIT"
    assert restored.verified is True
    assert restored.verification_method == "hython-cook"
    assert restored.verified_houdini_build == "21.0.630"
    assert restored.checksum == chunk.checksum


def test_checksum_unchanged_by_new_metadata():
    """New metadata fields must not affect the dedup checksum (content+code)."""
    base = ChunkV2(content="x", code_blocks=[CodeBlock(code="@a = 1;")])
    tagged = ChunkV2(
        content="x", code_blocks=[CodeBlock(code="@a = 1;")],
        license="MIT", domain="mpm", verified=True,
        verification_method="hython-cook",
    )
    assert base.checksum == tagged.checksum


def test_legacy_chunk_loads_without_new_fields():
    """A v2 dict written before this change (no new keys) still loads."""
    legacy = {
        "id": "old",
        "content": "c",
        "code_blocks": [{"code": "@P += 1;", "is_complete": True}],
        "vex_context": ["sop"],
        "difficulty": "beginner",
    }
    chunk = ChunkV2.from_dict(legacy)
    assert chunk.id == "old"
    assert chunk.license == ""          # default
    assert chunk.verified is False      # default
    assert chunk.domain == ""           # default


def test_v1_migration_still_works():
    v1 = {
        "id": "joy_of_vex_ep01_001",
        "code": "@Cd = @N;",
        "source_file": "https://www.youtube.com/watch?v=x",
        "classification": {"context": "SOP", "attributes_read": ["N"],
                           "attributes_written": ["Cd"]},
        "generation": {"prompt": "Set color", "difficulty": 1,
                       "explanation": "sets cd"},
        "quality": {"flagged_for_review": False},
    }
    chunk = migrate_v1_sample(v1)
    assert chunk.vex_context == ["sop"]
    assert chunk.attributes_read == ["N"]
    # Migrated chunks get the new defaults
    assert chunk.verified is False
    assert chunk.license == ""
