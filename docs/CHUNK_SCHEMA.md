# Chunk Schema

## Current Schema (VEXSample)

Defined in `pipeline/intake.py`. The dataclass exports via `to_dict()` into a nested JSON structure.

### Fields

| Field | Type | Location | Description |
|-------|------|----------|-------------|
| `id` | str | top | Unique ID: `"vex_{hash}"` or `"joy_of_vex_ep01_001"` |
| `code` | str | top | Raw VEX code |
| `source_file` | str | top | File path or YouTube URL |
| `source_line` | int | top | Line number in source (0 for non-file) |
| `hash` | str | top | SHA256[:16] for dedup |
| `created_at` | str | top | ISO timestamp |
| `stage` | str | top | Processing stage (pending/complete/failed) |
| `context` | str | classification | point/prim/vertex/detail/SOP/unknown |
| `context_confidence` | float | classification | 0.0-1.0 |
| `attributes_read` | list | classification | Attrs read (format varies!) |
| `attributes_written` | list | classification | Attrs written (format varies!) |
| `channels` | list | classification | ch() parameter references |
| `functions` | list | classification | VEX function calls |
| `bugs` | list | classification | Detected issues |
| `complexity` | str | classification | O(1)/O(n)/simple/moderate/complex |
| `topic` | str | classification | Ad-hoc label (color/math_operations/etc.) |
| `prompt` | str | generation | Natural language instruction |
| `alternative_prompts` | list | generation | Paraphrases (full sentences or keywords) |
| `explanation` | str | generation | 2-3 paragraph explanation |
| `difficulty` | int | generation | 1-10 or 1-5 depending on source |
| `flagged_for_review` | bool | quality | True if low confidence |
| `review_reason` | str | quality | Why flagged |

### Known Inconsistencies

| Issue | Pipeline Output | Joy of VEX Import |
|-------|----------------|-------------------|
| `context` | `"point"` (lowercase) | `"SOP"` (uppercase) |
| `complexity` | `"O(1)"`, `"O(n)"` | `"simple"`, `"moderate"` |
| `attributes_read` | `[{name, type, builtin}]` | `["N", "P"]` (flat strings) |
| `alternative_prompts` | Full sentences | Single keywords |
| `difficulty` | 1-10 scale | 1, 3, or 5 only |

---

## Target Schema (v2)

Defined in `pipeline/schema.py`. Flat structure, standardized enums, all fields have defaults for backward compatibility.

### Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| **Content** | | | |
| `id` | str | auto | Unique chunk ID (`{source_id}_{seq}` or `vex_{hash}`) |
| `content` | str | `""` | Prose content (explanations, context) |
| `code_blocks` | list[CodeBlock] | `[]` | Extracted VEX snippets, separated from prose |
| **Classification** | | | |
| `content_type` | ContentType | `"concept"` | concept / pattern / reference / troubleshooting / discussion |
| `difficulty` | Difficulty | `"beginner"` | beginner / intermediate / advanced / expert |
| `vex_context` | list[VEXContext] | `["sop"]` | sop / dop / cop / chop / cvex / material / solver |
| **Source Metadata** | | | |
| `source_id` | str | `""` | Maps to `config/sources.yaml` |
| `source_url` | str | `""` | Direct URL to source material |
| `source_authority` | float | `0.0` | 0.0-1.0, from sources.yaml |
| `title` | str | `""` | Human-readable chunk title |
| `section` | str | `""` | Section within source (e.g. "JoyOfVex Day 7") |
| **VEX-Specific** | | | |
| `functions_referenced` | list[str] | `[]` | `["pcopen", "pcfind", "pcclose"]` |
| `attributes_read` | list[str] | `[]` | `["P", "N", "Cd"]` (always flat strings) |
| `attributes_written` | list[str] | `[]` | `["pscale", "Cd"]` |
| `houdini_version_min` | str | `""` | Minimum Houdini version |
| `houdini_version_notes` | str | `""` | Deprecation or behavior change notes |
| `prerequisites` | list[str] | `[]` | Chunk IDs this depends on |
| **Pipeline Metadata** | | | |
| `created_at` | str | auto | ISO timestamp |
| `pipeline_version` | str | `"0.1.0"` | Pipeline version that generated this |
| `checksum` | str | auto | Full SHA256 of content + code_blocks for dedup |
| **Generation (from LLM)** | | | |
| `prompt` | str | `""` | Natural language instruction |
| `alternative_prompts` | list[str] | `[]` | 3-5 full-sentence paraphrases |
| `explanation` | str | `""` | 2-3 paragraph explanation |
| **Quality** | | | |
| `flagged_for_review` | bool | `False` | |
| `review_reason` | str | `""` | |
| `validation_warnings` | list[str] | `[]` | VEX syntax issues found |

### CodeBlock Schema

```python
{
    "code": str,           # The VEX snippet
    "line_context": str,   # Where in source this appeared
    "is_complete": bool,   # True if syntactically complete
}
```

### Enum Values

**ContentType**: `concept`, `pattern`, `reference`, `troubleshooting`, `discussion`

**Difficulty**: `beginner`, `intermediate`, `advanced`, `expert`

**VEXContext**: `sop`, `dop`, `cop`, `chop`, `cvex`, `material`, `solver`

### Migration from v1

The `pipeline/schema.py` module provides:
- `migrate_v1_sample(old_dict) -> ChunkV2` -- converts a v1 VEXSample dict to v2 format
- All v2 fields have defaults, so v1 data loads without errors
- The `content` field is populated from `explanation` (v1) or left empty
- The `code_blocks` field is populated from `code` (v1) as a single block
- Difficulty integers are mapped: 1-2 -> beginner, 3-4 -> intermediate, 5-7 -> advanced, 8-10 -> expert
- Complexity strings are normalized: "simple" -> "O(1)", "moderate" -> "O(n)", "complex" -> "O(n^2)"
