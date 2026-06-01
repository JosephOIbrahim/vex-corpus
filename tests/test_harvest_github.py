"""Tests for the license-aware GitHub harvester (Phase 1)."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.scrapers.harvest_github import (
    SourceMeta,
    assert_harvestable,
    extract_from_file,
    extract_md_vex_blocks,
    extract_vex_functions,
    harvest_repo,
    load_source_meta,
)


# --- license gate ---------------------------------------------------------

def test_load_real_green_source():
    meta = load_source_meta("thi-ng-vexed-generation")
    assert meta.license == "MIT"
    assert_harvestable(meta)  # must not raise


def test_refuse_reference_only_source():
    meta = load_source_meta("sidefx-vex-reference")
    with pytest.raises(PermissionError):
        assert_harvestable(meta)


def test_refuse_unlicensed_source():
    # jtomori-vex-tutorial intentionally has license: "" (TBC) in sources.yaml
    meta = load_source_meta("jtomori-vex-tutorial")
    with pytest.raises(PermissionError):
        assert_harvestable(meta)


def test_unknown_source_raises():
    with pytest.raises(KeyError):
        load_source_meta("does-not-exist")


# --- extraction -----------------------------------------------------------

def test_extract_md_vex_blocks():
    md = (
        "# Title\n\nsome prose\n\n"
        "## Remap\n\n```vex\nf@x = fit01(@x, 0, 1);\n```\n\n"
        "ignored ```python\nprint('no')\n```\n"
    )
    blocks = extract_md_vex_blocks(md)
    assert len(blocks) == 1
    assert "fit01" in blocks[0].code
    assert blocks[0].title == "Remap"


def test_extract_vex_functions():
    header = (
        "// returns a swirl vector\n"
        "vector vgSwirl(vector p; float s) {\n"
        "    return cross({0,1,0}, p) * s;\n"
        "}\n\n"
        "float vgRemap(float x; float a; float b) {\n"
        "    return fit(x, a, b, 0, 1);\n"
        "}\n"
    )
    funcs = extract_vex_functions(header)
    names = {f.title for f in funcs}
    assert names == {"vgSwirl", "vgRemap"}
    swirl = next(f for f in funcs if f.title == "vgSwirl")
    assert "cross" in swirl.code
    assert "swirl vector" in swirl.doc


def test_extract_handles_nested_braces():
    header = (
        "int vgLoop(int n) {\n"
        "    int t = 0;\n"
        "    for (int i = 0; i < n; i++) { t += i; }\n"
        "    return t;\n"
        "}\n"
    )
    funcs = extract_vex_functions(header)
    assert len(funcs) == 1
    assert funcs[0].code.count("{") == funcs[0].code.count("}")


def test_extract_from_file_dispatch(tmp_path):
    vfl = tmp_path / "single.vfl"
    vfl.write_text("@P.y += sin(@P.x);\n", encoding="utf-8")
    out = extract_from_file(vfl, vfl.read_text())
    assert len(out) == 1
    assert out[0].title == "single"


# --- end-to-end harvest ---------------------------------------------------

def _green_meta() -> SourceMeta:
    return SourceMeta(
        id="test-green", url="https://github.com/acme/lib.git",
        license="MIT", attribution="acme", authority=0.8,
        vex_contexts=["sop"],
    )


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "vgen.h").write_text(
        "// add two vectors\n"
        "vector vgAdd(vector a; vector b) { return a + b; }\n\n"
        "// scale a vector\n"
        "vector vgScale(vector v; float s) { return v * s; }\n",
        encoding="utf-8",
    )
    (repo / "README.md").write_text(
        "## Example\n\n```vex\n@Cd = set(1, 0, 0);\n```\n", encoding="utf-8",
    )
    (repo / "notes.txt").write_text("not vex", encoding="utf-8")
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text("vector ignored() { return 0; }",
                                          encoding="utf-8")
    return repo


def test_harvest_repo_end_to_end(tmp_path):
    repo = _make_repo(tmp_path)
    chunks = harvest_repo(repo, _green_meta(), commit="abc123",
                          domain="procedural_modeling", static_only=True)
    titles = {c.title for c in chunks}
    # two functions from the header + one md block; .txt and .git ignored
    assert titles == {"vgAdd", "vgScale", "Example"}

    for c in chunks:
        assert c.license == "MIT"
        assert c.source_id == "test-green"
        assert c.domain == "procedural_modeling"
        assert c.verification_method == "static-lint"
        assert c.verified is False  # static-lint never verifies

    add = next(c for c in chunks if c.title == "vgAdd")
    assert add.source_url == (
        "https://github.com/acme/lib/blob/abc123/vgen.h#L2-L2"
    )
    assert add.content == "add two vectors"


def test_harvest_dedupes_identical_code(tmp_path):
    repo = tmp_path / "dup"
    repo.mkdir()
    snippet = "vector vgAdd(vector a; vector b) { return a + b; }"
    (repo / "a.h").write_text(snippet, encoding="utf-8")
    (repo / "b.h").write_text(snippet, encoding="utf-8")
    chunks = harvest_repo(repo, _green_meta(), static_only=True)
    assert len(chunks) == 1  # identical code deduped by checksum


def test_harvest_refuses_bad_license(tmp_path):
    repo = _make_repo(tmp_path)
    bad = SourceMeta(id="bad", url="x", license="", attribution="",
                     authority=0.0, vex_contexts=["sop"])
    with pytest.raises(PermissionError):
        harvest_repo(repo, bad, static_only=True)
