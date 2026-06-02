"""Tests for the authored sample catalog and ingest."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.schema import REDISTRIBUTABLE_LICENSES, Domain
from scripts.authoring.build_authored import DEFAULTS, REQUIRED_FIELDS, _apply_defaults
from scripts.authoring.catalog import CATALOG
from scripts.import_authored import LICENSE, _to_chunk
import re

from scripts.quality.verify_vex import _strip_comments_and_strings, static_lint


def _all_samples():
    for _key, (domain, samples) in CATALOG.items():
        for s in samples:
            yield domain, s


def test_catalog_nonempty_and_covers_all_domains():
    domains = {domain for domain, _ in _all_samples()}
    for expected in {"procedural_modeling", "mpm", "look_development",
                     "apex", "solaris", "lighting", "tops"}:
        assert expected in domains


def test_every_domain_is_a_valid_domain_enum():
    valid = {d.value for d in Domain}
    for domain, _ in _all_samples():
        assert domain in valid


def test_ids_unique():
    ids = [s["id"] for _, s in _all_samples()]
    assert len(ids) == len(set(ids)), "duplicate sample ids"


def test_required_fields_present():
    for _domain, s in _all_samples():
        for field in REQUIRED_FIELDS:
            assert field in s, f"{s.get('id')} missing {field}"


def test_every_sample_passes_static_lint():
    for domain, s in _all_samples():
        rec = _apply_defaults(s, domain)
        ctx = rec["vex_context"][0] if rec["vex_context"] else "sop"
        res = static_lint(rec["code"], ctx)
        assert res.passed, f"{s['id']} failed lint: {res.errors}"
        assert not res.warnings, f"{s['id']} lint warnings: {res.warnings}"


def test_apex_samples_have_no_at_syntax():
    # APEX VEX must not use @ attribute syntax in actual code (comments that
    # *mention* '@' are fine -- the linter strips comments/strings first).
    for domain, s in _all_samples():
        if domain == "apex":
            executable = _strip_comments_and_strings(s["code"])
            assert not re.search(r"[a-zA-Z_]?@\w+", executable), \
                f"{s['id']} uses @ attribute syntax in APEX VEX"


def test_authored_license_is_redistributable():
    assert LICENSE in REDISTRIBUTABLE_LICENSES


def test_to_chunk_stamps_metadata():
    domain, s = next(_all_samples())
    rec = _apply_defaults(s, domain)
    chunk = _to_chunk(rec, static_only=True)
    assert chunk.license == "MIT"
    assert chunk.source_id == "authored-h21-samples"
    assert chunk.source_authority == 0.85
    assert chunk.houdini_version_min == "21.0"
    assert chunk.verification_method == "static-lint"
    # No Houdini in CI -> not verified, but also not flagged (lint passed)
    assert chunk.verified is False
    assert chunk.flagged_for_review is False
    assert chunk.code_blocks and chunk.code_blocks[0].code


def test_defaults_sane():
    assert DEFAULTS["houdini_version_min"] == "21.0"
    assert DEFAULTS["vex_context"] == ["sop"]
