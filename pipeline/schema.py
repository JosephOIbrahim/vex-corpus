"""Canonical chunk schema for the VEX corpus.

This module defines the v2 chunk schema used across the pipeline.
All new code should import schema types from here.

The schema is backward-compatible: v1 VEXSample dicts can be migrated
via ``migrate_v1_sample()`` with no data loss.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

# Pipeline version stamped on every chunk
PIPELINE_VERSION = "0.2.0"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ContentType(str, Enum):
    """What kind of knowledge a chunk represents."""
    CONCEPT = "concept"
    PATTERN = "pattern"
    REFERENCE = "reference"
    TROUBLESHOOTING = "troubleshooting"
    DISCUSSION = "discussion"


class Difficulty(str, Enum):
    """Skill level required to understand the chunk."""
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class VEXContext(str, Enum):
    """Houdini VEX execution context."""
    SOP = "sop"
    DOP = "dop"
    COP = "cop"
    CHOP = "chop"
    CVEX = "cvex"
    MATERIAL = "material"
    SOLVER = "solver"


# ---------------------------------------------------------------------------
# Code block
# ---------------------------------------------------------------------------

@dataclass
class CodeBlock:
    """A single VEX code snippet extracted from a chunk."""
    code: str = ""
    line_context: str = ""
    is_complete: bool = True

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "line_context": self.line_context,
            "is_complete": self.is_complete,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CodeBlock":
        return cls(
            code=d.get("code", ""),
            line_context=d.get("line_context", ""),
            is_complete=d.get("is_complete", True),
        )


# ---------------------------------------------------------------------------
# Chunk v2
# ---------------------------------------------------------------------------

@dataclass
class ChunkV2:
    """A single knowledge chunk in the VEX corpus (v2 schema).

    Every field has a sensible default so that partially-populated chunks
    (e.g. migrated v1 data) are always valid.
    """

    # --- Content ---
    id: str = ""
    content: str = ""
    code_blocks: list[CodeBlock] = field(default_factory=list)

    # --- Classification ---
    content_type: str = ContentType.CONCEPT.value
    difficulty: str = Difficulty.BEGINNER.value
    vex_context: list[str] = field(default_factory=lambda: [VEXContext.SOP.value])

    # --- Source metadata ---
    source_id: str = ""
    source_url: str = ""
    source_authority: float = 0.0
    title: str = ""
    section: str = ""

    # --- VEX-specific ---
    functions_referenced: list[str] = field(default_factory=list)
    attributes_read: list[str] = field(default_factory=list)
    attributes_written: list[str] = field(default_factory=list)
    houdini_version_min: str = ""
    houdini_version_notes: str = ""
    prerequisites: list[str] = field(default_factory=list)

    # --- Pipeline metadata ---
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    pipeline_version: str = PIPELINE_VERSION
    checksum: str = ""

    # --- Generation (from LLM) ---
    prompt: str = ""
    alternative_prompts: list[str] = field(default_factory=list)
    explanation: str = ""

    # --- Quality ---
    flagged_for_review: bool = False
    review_reason: str = ""
    validation_warnings: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.checksum:
            self.checksum = self._compute_checksum()

    def _compute_checksum(self) -> str:
        """SHA256 over content + code for dedup detection."""
        parts = [self.content]
        for block in self.code_blocks:
            parts.append(block.code)
        blob = "\n".join(parts).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def to_dict(self) -> dict:
        """Serialize to a flat dictionary for JSON export."""
        return {
            "id": self.id,
            "content": self.content,
            "code_blocks": [b.to_dict() for b in self.code_blocks],
            "content_type": self.content_type,
            "difficulty": self.difficulty,
            "vex_context": self.vex_context,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "source_authority": self.source_authority,
            "title": self.title,
            "section": self.section,
            "functions_referenced": self.functions_referenced,
            "attributes_read": self.attributes_read,
            "attributes_written": self.attributes_written,
            "houdini_version_min": self.houdini_version_min,
            "houdini_version_notes": self.houdini_version_notes,
            "prerequisites": self.prerequisites,
            "created_at": self.created_at,
            "pipeline_version": self.pipeline_version,
            "checksum": self.checksum,
            "prompt": self.prompt,
            "alternative_prompts": self.alternative_prompts,
            "explanation": self.explanation,
            "flagged_for_review": self.flagged_for_review,
            "review_reason": self.review_reason,
            "validation_warnings": self.validation_warnings,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ChunkV2":
        """Deserialize from a flat dictionary."""
        code_blocks = [CodeBlock.from_dict(b) for b in d.get("code_blocks", [])]
        chunk = cls(
            id=d.get("id", ""),
            content=d.get("content", ""),
            code_blocks=code_blocks,
            content_type=d.get("content_type", ContentType.CONCEPT.value),
            difficulty=d.get("difficulty", Difficulty.BEGINNER.value),
            vex_context=d.get("vex_context", [VEXContext.SOP.value]),
            source_id=d.get("source_id", ""),
            source_url=d.get("source_url", ""),
            source_authority=d.get("source_authority", 0.0),
            title=d.get("title", ""),
            section=d.get("section", ""),
            functions_referenced=d.get("functions_referenced", []),
            attributes_read=d.get("attributes_read", []),
            attributes_written=d.get("attributes_written", []),
            houdini_version_min=d.get("houdini_version_min", ""),
            houdini_version_notes=d.get("houdini_version_notes", ""),
            prerequisites=d.get("prerequisites", []),
            created_at=d.get("created_at", ""),
            pipeline_version=d.get("pipeline_version", PIPELINE_VERSION),
            checksum=d.get("checksum", ""),
            prompt=d.get("prompt", ""),
            alternative_prompts=d.get("alternative_prompts", []),
            explanation=d.get("explanation", ""),
            flagged_for_review=d.get("flagged_for_review", False),
            review_reason=d.get("review_reason", ""),
            validation_warnings=d.get("validation_warnings", []),
        )
        if not chunk.checksum:
            chunk.checksum = chunk._compute_checksum()
        return chunk


# ---------------------------------------------------------------------------
# Difficulty mapping helpers
# ---------------------------------------------------------------------------

def _map_difficulty_int(value: int) -> str:
    """Map numeric difficulty (1-10) to named level."""
    if value <= 2:
        return Difficulty.BEGINNER.value
    if value <= 4:
        return Difficulty.INTERMEDIATE.value
    if value <= 7:
        return Difficulty.ADVANCED.value
    return Difficulty.EXPERT.value


def _map_difficulty_str(value: str) -> str:
    """Normalize string difficulty to enum value."""
    value = value.strip().lower()
    if value in (d.value for d in Difficulty):
        return value
    # Legacy mappings
    mapping = {
        "simple": Difficulty.BEGINNER.value,
        "easy": Difficulty.BEGINNER.value,
        "moderate": Difficulty.INTERMEDIATE.value,
        "medium": Difficulty.INTERMEDIATE.value,
        "hard": Difficulty.ADVANCED.value,
        "complex": Difficulty.ADVANCED.value,
        "very hard": Difficulty.EXPERT.value,
    }
    return mapping.get(value, Difficulty.BEGINNER.value)


def _normalize_complexity(value: str) -> str:
    """Normalize complexity strings to big-O notation."""
    mapping = {
        "simple": "O(1)",
        "moderate": "O(n)",
        "complex": "O(n^2)",
    }
    return mapping.get(value.strip().lower(), value)


def _normalize_attrs(attrs: list) -> list[str]:
    """Normalize attribute lists to flat strings without @ prefix."""
    result = []
    for a in attrs:
        if isinstance(a, dict):
            name = a.get("name", "")
        else:
            name = str(a)
        name = name.lstrip("@")
        if name:
            result.append(name)
    return sorted(set(result))


def _normalize_context(ctx: str) -> list[str]:
    """Normalize a v1 context string to a list of VEXContext values."""
    ctx = ctx.strip().lower()
    if ctx in ("point", "prim", "vertex", "detail", "sop", ""):
        return [VEXContext.SOP.value]
    mapping = {
        "dop": [VEXContext.DOP.value],
        "cop": [VEXContext.COP.value],
        "chop": [VEXContext.CHOP.value],
        "cvex": [VEXContext.CVEX.value],
        "material": [VEXContext.MATERIAL.value],
        "shader": [VEXContext.MATERIAL.value],
        "solver": [VEXContext.SOLVER.value],
    }
    return mapping.get(ctx, [VEXContext.SOP.value])


def _infer_section_from_id(chunk_id: str) -> str:
    """Infer a section label from Joy of VEX style IDs."""
    # "joy_of_vex_ep01_001" -> "Joy of VEX Episode 01"
    import re
    m = re.match(r"joy_of_vex_ep(\d+)", chunk_id)
    if m:
        return f"Joy of VEX Day {int(m.group(1))}"
    return ""


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def migrate_v1_sample(d: dict) -> ChunkV2:
    """Convert a v1 VEXSample dict (nested or flat) to ChunkV2.

    Handles both the nested ``to_dict()`` format from the pipeline and the
    flat format from the Joy of VEX import.
    """
    # Resolve nested vs flat structure
    classification = d.get("classification", {})
    generation = d.get("generation", {})
    quality = d.get("quality", {})

    # Code -> code_blocks
    code = d.get("code", "")
    code_blocks = []
    if code:
        code_blocks.append(CodeBlock(code=code, is_complete=True))

    # Difficulty
    raw_diff = generation.get("difficulty", d.get("difficulty", 0))
    if isinstance(raw_diff, int) and raw_diff > 0:
        difficulty = _map_difficulty_int(raw_diff)
    elif isinstance(raw_diff, str):
        difficulty = _map_difficulty_str(raw_diff)
    else:
        difficulty = Difficulty.BEGINNER.value

    # Attributes
    attrs_read = _normalize_attrs(
        classification.get("attributes_read", d.get("attributes_read", []))
    )
    attrs_written = _normalize_attrs(
        classification.get("attributes_written", d.get("attributes_written", []))
    )

    # VEX context
    raw_ctx = classification.get("context", d.get("context", ""))
    vex_context = _normalize_context(raw_ctx)

    # Functions
    functions = classification.get("functions", d.get("functions", []))
    if not functions:
        functions = []

    # Source
    source_file = d.get("source_file", "")
    source_id = ""
    source_url = ""
    if "youtube.com" in source_file:
        source_id = "joy-of-vex-youtube"
        source_url = source_file
    elif source_file:
        source_url = source_file

    # Title from prompt
    title = generation.get("prompt", d.get("prompt", ""))

    # Section from ID
    chunk_id = d.get("id", "")
    section = _infer_section_from_id(chunk_id)

    chunk = ChunkV2(
        id=chunk_id,
        content=generation.get("explanation", d.get("explanation", "")),
        code_blocks=code_blocks,
        content_type=ContentType.CONCEPT.value,  # Will be enriched by auto-tagger
        difficulty=difficulty,
        vex_context=vex_context,
        source_id=source_id,
        source_url=source_url,
        source_authority=0.9 if source_id == "joy-of-vex-youtube" else 0.0,
        title=title,
        section=section,
        functions_referenced=functions,
        attributes_read=attrs_read,
        attributes_written=attrs_written,
        created_at=d.get("created_at", ""),
        pipeline_version=PIPELINE_VERSION,
        prompt=generation.get("prompt", d.get("prompt", "")),
        alternative_prompts=generation.get("alternative_prompts",
                                           d.get("alternative_prompts", [])),
        explanation=generation.get("explanation", d.get("explanation", "")),
        flagged_for_review=quality.get("flagged_for_review",
                                       d.get("flagged_for_review", False)),
        review_reason=quality.get("review_reason",
                                  d.get("review_reason", "")),
    )
    return chunk
