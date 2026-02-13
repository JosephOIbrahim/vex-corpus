"""Pydantic models for VEX corpus task processing."""

from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class TaskType(str, Enum):
    """Available VEX processing task types."""

    # Tier 1: Classification (nemotron-mini)
    CLASSIFY_WRANGLE = "classify_wrangle"
    EXTRACT_ATTRIBUTES = "extract_attributes"
    EXTRACT_FUNCTIONS = "extract_functions"
    DETECT_BUGS = "detect_bugs"
    ESTIMATE_COMPLEXITY = "estimate_complexity"
    CLASSIFY_TOPIC = "classify_topic"

    # Tier 2: Generation (nemotron 70B)
    GENERATE_PROMPT = "generate_prompt"
    GENERATE_EXPLANATION = "generate_explanation"
    INJECT_BUG = "inject_bug"
    PARAPHRASE_PROMPT = "paraphrase_prompt"
    RATE_DIFFICULTY = "rate_difficulty"
    INFER_CONTEXT = "infer_context"


class WrangleContext(str, Enum):
    """VEX wrangle execution contexts."""

    POINT = "point"
    PRIM = "prim"
    VERTEX = "vertex"
    DETAIL = "detail"
    UNKNOWN = "unknown"


class TaskStatus(str, Enum):
    """Task execution status."""

    SUCCESS = "success"
    ERROR = "error"
    UNCERTAIN = "uncertain"


class VEXTask(BaseModel):
    """Input task for processing."""

    task: TaskType
    id: str = Field(default="")
    input: dict[str, Any]


class ClassifyWrangleResult(BaseModel):
    """Result from classify_wrangle task."""

    context: WrangleContext
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)


class AttributeInfo(BaseModel):
    """Information about a VEX attribute."""

    name: str
    type: str = "unknown"
    context: str = "unknown"
    builtin: bool = False


class ChannelInfo(BaseModel):
    """Information about a channel reference."""

    name: str
    type: str = "float"
    function: str = "ch"


class ExtractAttributesResult(BaseModel):
    """Result from extract_attributes task."""

    reads: list[AttributeInfo] = Field(default_factory=list)
    writes: list[AttributeInfo] = Field(default_factory=list)
    channels: list[ChannelInfo] = Field(default_factory=list)


class FunctionInfo(BaseModel):
    """Information about a VEX function call."""

    name: str
    category: str = "other"
    count: int = 1


class ExtractFunctionsResult(BaseModel):
    """Result from extract_functions task."""

    functions: list[FunctionInfo] = Field(default_factory=list)
    has_loops: bool = False
    has_conditionals: bool = False
    custom_functions: list[str] = Field(default_factory=list)


class BugInfo(BaseModel):
    """Information about a detected bug."""

    type: str
    severity: str = "warning"
    location: str = ""
    description: str = ""
    suggestion: str = ""


class DetectBugsResult(BaseModel):
    """Result from detect_bugs task."""

    bugs: list[BugInfo] = Field(default_factory=list)
    overall_quality: str = "clean"


class TaskResult(BaseModel):
    """Standard response wrapper for all tasks."""

    task_id: str = ""
    status: TaskStatus = TaskStatus.SUCCESS
    result: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    processing_notes: str = ""
    model_used: str = ""
    fallback_used: str | None = None


class BatchResult(BaseModel):
    """Result from batch processing."""

    results: list[TaskResult] = Field(default_factory=list)
    batch_stats: dict[str, Any] = Field(default_factory=dict)


TIER1_TASKS: set[TaskType] = {
    TaskType.CLASSIFY_WRANGLE,
    TaskType.EXTRACT_ATTRIBUTES,
    TaskType.EXTRACT_FUNCTIONS,
    TaskType.DETECT_BUGS,
    TaskType.ESTIMATE_COMPLEXITY,
    TaskType.CLASSIFY_TOPIC,
}

TIER2_TASKS: set[TaskType] = {
    TaskType.GENERATE_PROMPT,
    TaskType.GENERATE_EXPLANATION,
    TaskType.INJECT_BUG,
    TaskType.PARAPHRASE_PROMPT,
    TaskType.RATE_DIFFICULTY,
    TaskType.INFER_CONTEXT,
}
