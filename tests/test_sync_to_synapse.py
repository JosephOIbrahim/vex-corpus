"""Tests for scripts/sync_to_synapse.py."""

import json
import sys
from pathlib import Path

import pytest

# Make scripts importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import sync_to_synapse as sync


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_chunk(
    id_: str = "chunk_001",
    title: str = "Test Chunk",
    llm_topic: str = "math_operations",
    difficulty: str = "beginner",
    source_id: str = "joy-of-vex-youtube",
    code: str = "@Cd = @N;",
    content: str = "Sets color from normal.",
    functions: list | None = None,
    attrs_read: list | None = None,
    attrs_written: list | None = None,
    alt_prompts: list | None = None,
    vex_context: list | None = None,
) -> dict:
    """Build a minimal corpus chunk for testing."""
    return {
        "id": id_,
        "title": title,
        "llm_topic": llm_topic,
        "difficulty": difficulty,
        "source_id": source_id,
        "content": content,
        "code_blocks": [{"code": code, "is_complete": True, "line_context": ""}],
        "functions_referenced": functions or [],
        "attributes_read": attrs_read or [],
        "attributes_written": attrs_written or [],
        "alternative_prompts": alt_prompts or [],
        "vex_context": vex_context or ["sop"],
    }


@pytest.fixture
def sample_chunks():
    """12 chunks across 2 topics: one major (10+), one small (<10)."""
    major = [
        _make_chunk(id_=f"math_{i:03d}", title=f"Math Example {i}",
                    llm_topic="math_operations", difficulty=d,
                    code=f"float x = {i};", content=f"Math example {i}.",
                    functions=["sin", "cos"], attrs_read=["P"],
                    attrs_written=["Cd"], alt_prompts=["trig"])
        for i, d in zip(range(10), ["beginner"] * 4 + ["intermediate"] * 4 + ["advanced"] * 2)
    ]
    small = [
        _make_chunk(id_="str_001", title="String Op",
                    llm_topic="string_operations", difficulty="beginner",
                    code='string s = "hello";', content="String example.",
                    functions=["concat"]),
        _make_chunk(id_="str_002", title="String Format",
                    llm_topic="string_operations", difficulty="intermediate",
                    code='string s = sprintf("%g", x);', content="Format string."),
    ]
    return major + small


@pytest.fixture
def corpus_jsonl(tmp_path, sample_chunks):
    """Write sample chunks to a JSONL file, return path."""
    p = tmp_path / "merged_corpus.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for chunk in sample_chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    return p


@pytest.fixture
def synapse_tree(tmp_path):
    """Create a minimal Synapse directory tree with pre-existing metadata."""
    ref_dir = tmp_path / "rag" / "skills" / "houdini21-reference"
    meta_dir = tmp_path / "rag" / "documentation" / "_metadata"
    ref_dir.mkdir(parents=True)
    meta_dir.mkdir(parents=True)

    # Pre-existing curated reference file
    (ref_dir / "joy_of_vex_color.md").write_text("# Joy of VEX: Color\n", encoding="utf-8")

    # Pre-existing semantic index
    existing_index = {
        "karma_rendering": {
            "summary": "Karma rendering",
            "description": "Karma renderer docs.",
            "keywords": ["karma", "xpu"],
        },
        "vex_corpus_old_stale": {
            "summary": "Stale entry from previous sync",
            "description": "Should be removed.",
            "keywords": ["stale"],
        },
    }
    (meta_dir / "semantic_index.json").write_text(
        json.dumps(existing_index, indent=2), encoding="utf-8"
    )

    # Pre-existing relevance map
    existing_map = {
        "karma_rendering": "render_agent",
        "vex_corpus_old_stale": "sop_agent",
    }
    (meta_dir / "agent_relevance_map.json").write_text(
        json.dumps(existing_map, indent=2), encoding="utf-8"
    )

    return tmp_path


# ---------------------------------------------------------------------------
# Unit tests: helpers
# ---------------------------------------------------------------------------

class TestTruncateCode:
    def test_short_code_unchanged(self):
        code = "float x = 1;\nfloat y = 2;"
        assert sync.truncate_code(code, max_lines=8) == code

    def test_exact_limit_unchanged(self):
        code = "\n".join(f"line {i}" for i in range(8))
        assert sync.truncate_code(code, max_lines=8) == code

    def test_long_code_truncated(self):
        code = "\n".join(f"line {i}" for i in range(12))
        result = sync.truncate_code(code, max_lines=3)
        lines = result.split("\n")
        assert len(lines) == 4  # 3 code + "// ..."
        assert lines[-1] == "// ..."
        assert lines[0] == "line 0"

    def test_trailing_newlines_stripped(self):
        assert sync.truncate_code("x = 1;\n\n\n") == "x = 1;"


class TestSortKey:
    def test_difficulty_ordering(self):
        beginner = _make_chunk(difficulty="beginner")
        advanced = _make_chunk(difficulty="advanced")
        assert sync.sort_key(beginner) < sync.sort_key(advanced)

    def test_unknown_difficulty_sorts_last(self):
        expert = _make_chunk(difficulty="expert")
        unknown = _make_chunk(difficulty="weird")
        assert sync.sort_key(expert) < sync.sort_key(unknown)

    def test_same_difficulty_sorts_by_source_then_id(self):
        a = _make_chunk(difficulty="beginner", source_id="aaa", id_="z")
        b = _make_chunk(difficulty="beginner", source_id="bbb", id_="a")
        assert sync.sort_key(a) < sync.sort_key(b)


class TestCollectKeywords:
    def test_harvests_from_all_fields(self):
        chunks = [_make_chunk(
            functions=["pcopen"],
            attrs_read=["P"],
            attrs_written=["Cd"],
            alt_prompts=["nearest neighbor"],
            vex_context=["sop"],
        )]
        kw = sync.collect_keywords(chunks)
        assert "pcopen" in kw
        assert "p" in kw
        assert "cd" in kw
        assert "nearest neighbor" in kw
        assert "sop" in kw

    def test_deduplication(self):
        chunks = [
            _make_chunk(functions=["sin"], attrs_read=["P"]),
            _make_chunk(functions=["sin"], attrs_read=["P"]),
        ]
        kw = sync.collect_keywords(chunks)
        assert kw.count("sin") == 1
        assert kw.count("p") == 1

    def test_capped_at_max(self):
        # Make a chunk with tons of keywords
        chunks = [_make_chunk(alt_prompts=[f"keyword_{i}" for i in range(100)])]
        kw = sync.collect_keywords(chunks)
        assert len(kw) <= sync.MAX_KEYWORDS

    def test_sorted_output(self):
        chunks = [_make_chunk(functions=["zzz", "aaa", "mmm"])]
        kw = sync.collect_keywords(chunks)
        assert kw == sorted(kw)

    def test_empty_chunks(self):
        assert sync.collect_keywords([]) == []


class TestGroupByTopic:
    def test_groups_by_llm_topic(self):
        chunks = [
            _make_chunk(llm_topic="math_operations"),
            _make_chunk(llm_topic="math_operations"),
            _make_chunk(llm_topic="color_operations"),
        ]
        groups = sync.group_by_topic(chunks)
        assert len(groups) == 2
        assert len(groups["math_operations"]) == 2
        assert len(groups["color_operations"]) == 1

    def test_skips_unenriched(self):
        chunks = [
            _make_chunk(llm_topic="math_operations"),
            _make_chunk(llm_topic=""),
        ]
        # Remove llm_topic entirely from second chunk
        del chunks[1]["llm_topic"]
        groups = sync.group_by_topic(chunks)
        assert len(groups) == 1

    def test_empty_input(self):
        assert sync.group_by_topic([]) == {}


class TestPartitionTopics:
    def test_splits_major_and_misc(self, sample_chunks):
        groups = sync.group_by_topic(sample_chunks)
        major, misc = sync.partition_topics(groups)
        assert "math_operations" in major
        assert len(major["math_operations"]) == 10
        assert len(misc) == 2  # string_operations chunks

    def test_empty_groups(self):
        major, misc = sync.partition_topics({})
        assert major == {}
        assert misc == []


# ---------------------------------------------------------------------------
# Unit tests: markdown rendering
# ---------------------------------------------------------------------------

class TestRenderTopicMd:
    def test_has_h1_with_label(self):
        chunks = [_make_chunk(llm_topic="color_operations")]
        md = sync.render_topic_md("color_operations", "Color Operations", chunks)
        assert md.startswith("# VEX Corpus: Color Operations")

    def test_has_blockquote_with_count(self):
        chunks = [_make_chunk() for _ in range(5)]
        md = sync.render_topic_md("test", "Test", chunks)
        assert "> 5 examples from vex-corpus." in md

    def test_difficulty_sections(self):
        chunks = [
            _make_chunk(id_="b1", difficulty="beginner"),
            _make_chunk(id_="i1", difficulty="intermediate"),
        ]
        md = sync.render_topic_md("test", "Test", chunks)
        assert "## Beginner (1 examples)" in md
        assert "## Intermediate (1 examples)" in md

    def test_code_blocks_rendered(self):
        chunks = [_make_chunk(code="float x = sin(@P.x);")]
        md = sync.render_topic_md("test", "Test", chunks)
        assert "```vex" in md
        assert "float x = sin(@P.x);" in md
        assert "```" in md

    def test_empty_code_skipped(self):
        chunks = [_make_chunk(code="")]
        md = sync.render_topic_md("test", "Test", chunks)
        assert "```vex" not in md

    def test_description_first_sentence(self):
        chunks = [_make_chunk(content="First sentence. Second sentence. Third.")]
        md = sync.render_topic_md("test", "Test", chunks)
        assert "First sentence." in md
        assert "Second sentence" not in md

    def test_long_description_truncated(self):
        long_desc = "A" * 250 + ". Next sentence."
        chunks = [_make_chunk(content=long_desc)]
        md = sync.render_topic_md("test", "Test", chunks)
        assert "..." in md

    def test_unknown_difficulty_labeled_uncategorized(self):
        # "unknown" is the sentinel value that triggers "Uncategorized" label;
        # unrecognized values like "weird" are simply not rendered (by design,
        # since the loop only iterates known difficulty levels + "unknown")
        chunks = [_make_chunk(difficulty="unknown")]
        md = sync.render_topic_md("test", "Test", chunks)
        assert "## Uncategorized" in md

    def test_deterministic_output(self):
        """Same input produces identical output across calls."""
        chunks = [
            _make_chunk(id_="a", difficulty="intermediate"),
            _make_chunk(id_="b", difficulty="beginner"),
        ]
        md1 = sync.render_topic_md("test", "Test", chunks)
        md2 = sync.render_topic_md("test", "Test", chunks)
        assert md1 == md2


class TestRenderMiscMd:
    def test_has_misc_title(self):
        chunks = [_make_chunk(llm_topic="string_operations")]
        md = sync.render_misc_md(chunks)
        assert "# VEX Corpus: Miscellaneous Topics" in md

    def test_groups_by_original_topic(self):
        chunks = [
            _make_chunk(id_="s1", llm_topic="string_operations"),
            _make_chunk(id_="q1", llm_topic="quaternion_operations"),
        ]
        md = sync.render_misc_md(chunks)
        assert "## Quaternion Operations" in md
        assert "## String Operations" in md


# ---------------------------------------------------------------------------
# Unit tests: metadata merging
# ---------------------------------------------------------------------------

class TestBuildSemanticEntry:
    def test_has_required_fields(self):
        chunks = [_make_chunk(functions=["sin"])]
        entry = sync.build_semantic_entry("vex_corpus_math", "Math Ops", chunks)
        assert "summary" in entry
        assert "description" in entry
        assert "keywords" in entry
        assert "reference_file" in entry

    def test_summary_includes_count(self):
        chunks = [_make_chunk() for _ in range(7)]
        entry = sync.build_semantic_entry("key", "Label", chunks)
        assert "(7 examples)" in entry["summary"]

    def test_reference_file_matches_key(self):
        entry = sync.build_semantic_entry("vex_corpus_test", "Test", [_make_chunk()])
        assert entry["reference_file"] == "vex_corpus_test"


class TestMergeSemanticIndex:
    def test_preserves_existing_entries(self, synapse_tree):
        index_path = synapse_tree / "rag" / "documentation" / "_metadata" / "semantic_index.json"
        new_entries = {
            "vex_corpus_math": {"summary": "Math", "keywords": ["math"]},
        }
        merged = sync.merge_semantic_index(index_path, new_entries)
        assert "karma_rendering" in merged

    def test_removes_stale_vex_corpus_entries(self, synapse_tree):
        index_path = synapse_tree / "rag" / "documentation" / "_metadata" / "semantic_index.json"
        new_entries = {
            "vex_corpus_math": {"summary": "Math", "keywords": ["math"]},
        }
        merged = sync.merge_semantic_index(index_path, new_entries)
        assert "vex_corpus_old_stale" not in merged

    def test_adds_new_entries(self, synapse_tree):
        index_path = synapse_tree / "rag" / "documentation" / "_metadata" / "semantic_index.json"
        new_entries = {
            "vex_corpus_math": {"summary": "Math", "keywords": ["math"]},
            "vex_corpus_color": {"summary": "Color", "keywords": ["color"]},
        }
        merged = sync.merge_semantic_index(index_path, new_entries)
        assert "vex_corpus_math" in merged
        assert "vex_corpus_color" in merged

    def test_handles_missing_file(self, tmp_path):
        missing = tmp_path / "nonexistent.json"
        merged = sync.merge_semantic_index(missing, {"vex_corpus_x": {"k": "v"}})
        assert merged == {"vex_corpus_x": {"k": "v"}}


class TestMergeRelevanceMap:
    def test_preserves_existing_entries(self, synapse_tree):
        map_path = synapse_tree / "rag" / "documentation" / "_metadata" / "agent_relevance_map.json"
        merged = sync.merge_relevance_map(map_path, ["vex_corpus_math"])
        assert merged["karma_rendering"] == "render_agent"

    def test_removes_stale_entries(self, synapse_tree):
        map_path = synapse_tree / "rag" / "documentation" / "_metadata" / "agent_relevance_map.json"
        merged = sync.merge_relevance_map(map_path, ["vex_corpus_math"])
        assert "vex_corpus_old_stale" not in merged

    def test_new_entries_route_to_sop_agent(self, synapse_tree):
        map_path = synapse_tree / "rag" / "documentation" / "_metadata" / "agent_relevance_map.json"
        merged = sync.merge_relevance_map(map_path, ["vex_corpus_math", "vex_corpus_color"])
        assert merged["vex_corpus_math"] == "sop_agent"
        assert merged["vex_corpus_color"] == "sop_agent"

    def test_handles_missing_file(self, tmp_path):
        missing = tmp_path / "nonexistent.json"
        merged = sync.merge_relevance_map(missing, ["vex_corpus_x"])
        assert merged == {"vex_corpus_x": "sop_agent"}


class TestAtomicWriteJson:
    def test_writes_valid_json(self, tmp_path):
        path = tmp_path / "test.json"
        data = {"b": 2, "a": 1}
        sync.atomic_write_json(path, data)
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded == data

    def test_sorted_keys(self, tmp_path):
        path = tmp_path / "test.json"
        sync.atomic_write_json(path, {"z": 1, "a": 2, "m": 3})
        text = path.read_text(encoding="utf-8")
        assert text.index('"a"') < text.index('"m"') < text.index('"z"')

    def test_overwrites_existing(self, tmp_path):
        path = tmp_path / "test.json"
        path.write_text('{"old": true}', encoding="utf-8")
        sync.atomic_write_json(path, {"new": True})
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded == {"new": True}


class TestCorpusHash:
    def test_deterministic(self, corpus_jsonl):
        h1 = sync.corpus_hash(corpus_jsonl)
        h2 = sync.corpus_hash(corpus_jsonl)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_changes_with_content(self, tmp_path):
        p = tmp_path / "a.jsonl"
        p.write_text('{"x":1}\n', encoding="utf-8")
        h1 = sync.corpus_hash(p)
        p.write_text('{"x":2}\n', encoding="utf-8")
        h2 = sync.corpus_hash(p)
        assert h1 != h2


# ---------------------------------------------------------------------------
# Integration tests: run_sync
# ---------------------------------------------------------------------------

class TestRunSync:
    def test_full_sync(self, corpus_jsonl, synapse_tree):
        sync.run_sync(corpus_jsonl, synapse_tree, dry_run=False, force=True)

        ref_dir = synapse_tree / "rag" / "skills" / "houdini21-reference"
        meta_dir = synapse_tree / "rag" / "documentation" / "_metadata"

        # Major topic file created
        assert (ref_dir / "vex_corpus_math_operations.md").exists()
        # Misc file created (string_operations < 10 chunks)
        assert (ref_dir / "vex_corpus_misc.md").exists()
        # Curated file untouched
        assert (ref_dir / "joy_of_vex_color.md").exists()

        # Semantic index merged correctly
        index = json.loads((meta_dir / "semantic_index.json").read_text(encoding="utf-8"))
        assert "karma_rendering" in index  # preserved
        assert "vex_corpus_old_stale" not in index  # stale removed
        assert "vex_corpus_math_operations" in index  # new added
        assert "vex_corpus_misc" in index

        # Relevance map merged correctly
        rmap = json.loads((meta_dir / "agent_relevance_map.json").read_text(encoding="utf-8"))
        assert rmap["karma_rendering"] == "render_agent"  # preserved
        assert "vex_corpus_old_stale" not in rmap  # stale removed
        assert rmap["vex_corpus_math_operations"] == "sop_agent"

        # Manifest written
        manifest_path = ref_dir / ".vex_corpus_manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["chunks_total"] == 12
        assert manifest["topics_major"] == 1
        assert "corpus_hash" in manifest

    def test_dry_run_writes_nothing(self, corpus_jsonl, synapse_tree):
        ref_dir = synapse_tree / "rag" / "skills" / "houdini21-reference"
        files_before = set(ref_dir.iterdir())

        sync.run_sync(corpus_jsonl, synapse_tree, dry_run=True, force=True)

        files_after = set(ref_dir.iterdir())
        assert files_before == files_after

    def test_incremental_skip(self, corpus_jsonl, synapse_tree, capsys):
        # First sync
        sync.run_sync(corpus_jsonl, synapse_tree, dry_run=False, force=True)
        # Second sync without --force
        sync.run_sync(corpus_jsonl, synapse_tree, dry_run=False, force=False)
        captured = capsys.readouterr()
        assert "Corpus unchanged since last sync" in captured.out

    def test_force_overrides_skip(self, corpus_jsonl, synapse_tree, capsys):
        # First sync
        sync.run_sync(corpus_jsonl, synapse_tree, dry_run=False, force=True)
        # Second with --force
        sync.run_sync(corpus_jsonl, synapse_tree, dry_run=False, force=True)
        captured = capsys.readouterr()
        assert "Sync complete." in captured.out

    def test_stale_files_cleaned(self, corpus_jsonl, synapse_tree):
        ref_dir = synapse_tree / "rag" / "skills" / "houdini21-reference"
        # Plant a stale vex_corpus_ file
        stale = ref_dir / "vex_corpus_old_topic.md"
        stale.write_text("# Stale\n", encoding="utf-8")

        sync.run_sync(corpus_jsonl, synapse_tree, dry_run=False, force=True)

        assert not stale.exists()

    def test_idempotent(self, corpus_jsonl, synapse_tree):
        """Running sync twice produces identical output."""
        meta_dir = synapse_tree / "rag" / "documentation" / "_metadata"
        ref_dir = synapse_tree / "rag" / "skills" / "houdini21-reference"

        sync.run_sync(corpus_jsonl, synapse_tree, dry_run=False, force=True)
        index_1 = (meta_dir / "semantic_index.json").read_text(encoding="utf-8")
        map_1 = (meta_dir / "agent_relevance_map.json").read_text(encoding="utf-8")
        md_1 = (ref_dir / "vex_corpus_math_operations.md").read_text(encoding="utf-8")

        sync.run_sync(corpus_jsonl, synapse_tree, dry_run=False, force=True)
        index_2 = (meta_dir / "semantic_index.json").read_text(encoding="utf-8")
        map_2 = (meta_dir / "agent_relevance_map.json").read_text(encoding="utf-8")
        md_2 = (ref_dir / "vex_corpus_math_operations.md").read_text(encoding="utf-8")

        assert index_1 == index_2
        assert map_1 == map_2
        assert md_1 == md_2


class TestRunSyncMarkdownFormat:
    """Verify generated markdown matches Synapse conventions."""

    def test_math_md_structure(self, corpus_jsonl, synapse_tree):
        sync.run_sync(corpus_jsonl, synapse_tree, dry_run=False, force=True)
        ref_dir = synapse_tree / "rag" / "skills" / "houdini21-reference"
        md = (ref_dir / "vex_corpus_math_operations.md").read_text(encoding="utf-8")

        assert md.startswith("# VEX Corpus: Math Operations")
        assert "> 10 examples from vex-corpus." in md
        assert "## Beginner (4 examples)" in md
        assert "## Intermediate (4 examples)" in md
        assert "## Advanced (2 examples)" in md
        assert "```vex" in md

    def test_misc_md_structure(self, corpus_jsonl, synapse_tree):
        sync.run_sync(corpus_jsonl, synapse_tree, dry_run=False, force=True)
        ref_dir = synapse_tree / "rag" / "skills" / "houdini21-reference"
        md = (ref_dir / "vex_corpus_misc.md").read_text(encoding="utf-8")

        assert "# VEX Corpus: Miscellaneous Topics" in md
        assert "> 2 examples from vex-corpus (small topics)." in md
        assert "## String Operations (2 examples)" in md
