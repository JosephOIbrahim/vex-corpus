"""Tests for the VEX verification gate (static-lint path)."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.quality.verify_vex import static_lint, verify_code


def test_balanced_code_passes():
    res = static_lint("if (@P.y > 0) { @P.y += 1; }", "sop")
    assert res.passed
    assert res.method == "static-lint"


def test_unbalanced_braces_fail():
    res = static_lint("if (@P.y > 0) { @P.y += 1;", "sop")
    assert not res.passed
    assert any("braces" in e for e in res.errors)


def test_unbalanced_parens_fail():
    res = static_lint("@d = length(@P;", "sop")
    assert not res.passed
    assert any("parentheses" in e for e in res.errors)


def test_empty_code_fails():
    assert not static_lint("", "sop").passed
    assert not static_lint("   \n  ", "sop").passed


def test_apex_rejects_at_syntax():
    res = static_lint("@P.y += 1;", "apex")
    assert not res.passed
    assert any("APEX" in e for e in res.errors)


def test_apex_named_ports_pass():
    res = static_lint("result = lerp(a, b, t);", "apex")
    assert res.passed


def test_braces_in_string_not_miscounted():
    # The unmatched brace lives inside a string literal -> should still pass.
    res = static_lint('s@msg = "value: {";', "sop")
    assert res.passed


def test_braces_in_comment_not_miscounted():
    res = static_lint("// closing brace } in a comment\n@P.y += 1;", "sop")
    assert res.passed


def test_verify_code_static_only_uses_lint():
    res = verify_code("@P.y += 1;", "sop", static_only=True)
    assert res.method == "static-lint"
    assert res.passed
    # static-lint can never set verified=True
    assert res.verified is False


def test_vector_literal_passes():
    res = static_lint("v@v = {0, 0, 0};", "sop")
    assert res.passed
