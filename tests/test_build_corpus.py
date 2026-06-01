"""Tests for the canonical corpus builder (organize-for-ingestion)."""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import scripts.build_corpus as bc
from scripts.build_corpus import (
    build,
    build_manifest,
    load_source_registry,
    normalize_chunk,
)

REG = {
    "joy-of-vex-youtube": {"license": "CC-BY-SA-4.0", "attribution": "Estela",
                           "reference_only": False, "authority": 0.9},
    "sidefx-vex-reference": {"license": "proprietary", "attribution": "SideFX",
                             "reference_only": True, "authority": 1.0},
    "authored-h21-samples": {"license": "MIT", "attribution": "vex-corpus",
                             "reference_only": False, "authority": 0.85},
}


def _chunk(**kw):
    base = {"id": "c", "source_id": "joy-of-vex-youtube",
            "code_blocks": [{"code": "@x = 1;"}], "content": "x"}
    base.update(kw)
    return base


# --- registry -------------------------------------------------------------

def test_load_real_registry():
    reg = load_source_registry()
    assert reg["authored-h21-samples"]["license"] == "MIT"
    assert reg["sidefx-vex-reference"]["reference_only"] is True


# --- normalize ------------------------------------------------------------

def test_legacy_topiced_chunk():
    c = normalize_chunk(_chunk(llm_topic="math_operations"), REG)
    assert c["domain"] == "fundamentals"
    assert c["llm_topic"] == "math_operations"   # preserved
    assert c["license"] == "CC-BY-SA-4.0"         # backfilled
    assert c["redistribution_review"] is False
    assert c["verified"] is False
    assert c["checksum"]


def test_legacy_untopiced_chunk_gets_fallback_topic():
    c = normalize_chunk(_chunk(), REG)  # no llm_topic, no domain
    assert c["domain"] == "fundamentals"
    assert c["llm_topic"] == "uncategorized"      # never dropped


def test_authored_chunk_groups_under_domain():
    c = normalize_chunk(
        _chunk(source_id="authored-h21-samples", domain="mpm", license="MIT"),
        REG,
    )
    assert c["llm_topic"] == "mpm"                # domain becomes grouping key
    assert c["license"] == "MIT"                  # not overwritten


def test_reference_only_with_code_flagged():
    c = normalize_chunk(_chunk(source_id="sidefx-vex-reference"), REG)
    assert c["license"] == "proprietary"
    assert c["redistribution_review"] is True


def test_reference_only_without_code_not_flagged():
    c = normalize_chunk(
        _chunk(source_id="sidefx-vex-reference", code_blocks=[{"code": "  "}]),
        REG,
    )
    assert c["redistribution_review"] is False


def test_existing_license_not_overwritten():
    c = normalize_chunk(_chunk(license="Apache-2.0"), REG)
    assert c["license"] == "Apache-2.0"


# --- manifest -------------------------------------------------------------

def test_manifest_counts():
    chunks = [
        normalize_chunk(_chunk(id="a", llm_topic="math_operations"), REG),
        normalize_chunk(_chunk(id="b", source_id="authored-h21-samples",
                               domain="mpm", license="MIT"), REG),
        normalize_chunk(_chunk(id="c", source_id="sidefx-vex-reference"), REG),
    ]
    m = build_manifest(chunks, {"legacy": 2, "authored": 1})
    assert m["total_chunks"] == 3
    assert m["with_code"] == 3
    assert m["redistribution_review_chunks"] == 1
    assert m["by_domain"] == {"fundamentals": 2, "mpm": 1}
    assert m["by_license"]["MIT"] == 1
    assert m["by_license"]["proprietary"] == 1
    assert "grouping_key" in m["ingestion"]


# --- integration: dedup + ordering ---------------------------------------

def test_build_dedupes_across_inputs(tmp_path, monkeypatch):
    legacy = tmp_path / "merged.jsonl"
    authored = tmp_path / "authored.jsonl"
    dup_code = [{"code": "@dup = 1;"}]
    legacy.write_text(
        json.dumps({"id": "L1", "source_id": "joy-of-vex-youtube",
                    "llm_topic": "math_operations", "content": "x",
                    "code_blocks": dup_code}) + "\n", encoding="utf-8")
    authored.write_text(
        # same content+code => same checksum => deduped (legacy wins)
        json.dumps({"id": "A1", "source_id": "authored-h21-samples",
                    "domain": "mpm", "content": "x", "code_blocks": dup_code}) + "\n"
        + json.dumps({"id": "A2", "source_id": "authored-h21-samples",
                      "domain": "mpm", "content": "unique",
                      "code_blocks": [{"code": "@u = 2;"}]}) + "\n",
        encoding="utf-8")

    monkeypatch.setattr(bc, "LEGACY_CORPUS", legacy)
    monkeypatch.setattr(bc, "AUTHORED", authored)
    monkeypatch.setattr(bc, "HARVEST_DIR", tmp_path / "noexist")

    chunks, provenance = build(REG)
    ids = {c["id"] for c in chunks}
    assert ids == {"L1", "A2"}            # A1 deduped against L1
    assert provenance["legacy"] == 1
    assert provenance["authored"] == 1    # only the unique one counted
    # sorted by (domain, source, id): fundamentals(L1) before mpm(A2)
    assert [c["id"] for c in chunks] == ["L1", "A2"]
