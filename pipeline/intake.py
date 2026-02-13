"""VEX Corpus Intake Pipeline - Core processing engine."""

import asyncio
import json
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from ollama.src.client import OllamaClient
from ollama.src.dispatcher import TaskDispatcher
from ollama.src.queue import JobQueue
from ollama.src.models import TaskResult, TaskStatus


class ProcessingStage(str, Enum):
    """Stages of VEX sample processing."""
    PENDING = "pending"
    PARSING = "parsing"
    TIER1_CLASSIFICATION = "tier1_classification"
    TIER2_GENERATION = "tier2_generation"
    VALIDATION = "validation"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class VEXSample:
    """A VEX code sample for corpus processing."""
    id: str
    code: str
    source_file: str = ""
    source_line: int = 0

    # Metadata
    hash: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    # Processing state
    stage: ProcessingStage = ProcessingStage.PENDING

    # Tier 1 results (classification)
    context: str = ""
    context_confidence: float = 0.0
    attributes_read: list = field(default_factory=list)
    attributes_written: list = field(default_factory=list)
    channels: list = field(default_factory=list)
    functions: list = field(default_factory=list)
    bugs: list = field(default_factory=list)
    complexity: str = ""
    topic: str = ""

    # Tier 2 results (generation)
    prompt: str = ""
    alternative_prompts: list = field(default_factory=list)
    explanation: str = ""
    difficulty: int = 0

    # Quality flags
    flagged_for_review: bool = False
    review_reason: str = ""

    def __post_init__(self):
        if not self.hash:
            self.hash = hashlib.sha256(self.code.encode()).hexdigest()[:16]
        if not self.id:
            self.id = f"vex_{self.hash}"

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON export."""
        return {
            "id": self.id,
            "code": self.code,
            "source_file": self.source_file,
            "source_line": self.source_line,
            "hash": self.hash,
            "created_at": self.created_at,
            "stage": self.stage.value,
            "classification": {
                "context": self.context,
                "context_confidence": self.context_confidence,
                "attributes_read": self.attributes_read,
                "attributes_written": self.attributes_written,
                "channels": self.channels,
                "functions": self.functions,
                "bugs": self.bugs,
                "complexity": self.complexity,
                "topic": self.topic,
            },
            "generation": {
                "prompt": self.prompt,
                "alternative_prompts": self.alternative_prompts,
                "explanation": self.explanation,
                "difficulty": self.difficulty,
            },
            "quality": {
                "flagged_for_review": self.flagged_for_review,
                "review_reason": self.review_reason,
            }
        }

    def to_training_pair(self) -> dict | None:
        """Convert to training data format (prompt, completion)."""
        if not self.prompt or self.flagged_for_review:
            return None
        return {
            "prompt": self.prompt,
            "completion": self.code,
            "metadata": {
                "context": self.context,
                "difficulty": self.difficulty,
                "topic": self.topic,
            }
        }


class VEXParser:
    """Parse VEX code from various sources."""

    # Regex patterns for VEX extraction
    WRANGLE_PATTERN = re.compile(
        r'(?:^|\n)\s*//\s*(?:VEX|wrangle|Wrangle)[^\n]*\n(.*?)(?=\n\s*//|\Z)',
        re.DOTALL
    )

    CODE_BLOCK_PATTERN = re.compile(
        r'```(?:vex|c\+\+|cpp)?\n(.*?)```',
        re.DOTALL
    )

    @classmethod
    def extract_from_file(cls, file_path: Path) -> list[VEXSample]:
        """Extract VEX samples from a file."""
        samples = []
        content = file_path.read_text(encoding='utf-8', errors='replace')

        if file_path.suffix in ('.vex', '.h', '.vfl'):
            # Raw VEX file - treat entire content as one sample
            samples.append(VEXSample(
                id="",
                code=content.strip(),
                source_file=str(file_path),
                source_line=1,
            ))
        elif file_path.suffix in ('.hip', '.hipnc', '.hiplc'):
            # Houdini file - would need proper parsing
            # For now, skip binary files
            pass
        elif file_path.suffix in ('.md', '.txt'):
            # Markdown/text - extract code blocks
            for match in cls.CODE_BLOCK_PATTERN.finditer(content):
                code = match.group(1).strip()
                if cls._looks_like_vex(code):
                    line_num = content[:match.start()].count('\n') + 1
                    samples.append(VEXSample(
                        id="",
                        code=code,
                        source_file=str(file_path),
                        source_line=line_num,
                    ))
        elif file_path.suffix == '.json':
            # JSON - look for code fields
            try:
                data = json.loads(content)
                samples.extend(cls._extract_from_json(data, str(file_path)))
            except json.JSONDecodeError:
                pass

        return samples

    @classmethod
    def _looks_like_vex(cls, code: str) -> bool:
        """Check if code looks like VEX."""
        vex_indicators = [
            '@P', '@N', '@Cd', '@ptnum', '@primnum',
            'ch(', 'chf(', 'chi(', 'chv(',
            'point(', 'prim(', 'setpointattrib(',
            'pcopen(', 'pcfind(', 'nearpoints(',
            'vector ', 'float ', 'int ',
        ]
        return any(ind in code for ind in vex_indicators)

    @classmethod
    def _extract_from_json(cls, data: Any, source: str, samples: list = None) -> list[VEXSample]:
        """Recursively extract VEX from JSON data."""
        if samples is None:
            samples = []

        if isinstance(data, dict):
            for key, value in data.items():
                if key in ('code', 'vex', 'vex_code', 'snippet') and isinstance(value, str):
                    if cls._looks_like_vex(value):
                        samples.append(VEXSample(
                            id=data.get('id', ''),
                            code=value.strip(),
                            source_file=source,
                        ))
                else:
                    cls._extract_from_json(value, source, samples)
        elif isinstance(data, list):
            for item in data:
                cls._extract_from_json(item, source, samples)

        return samples


class IntakePipeline:
    """Main intake pipeline for VEX corpus processing."""

    def __init__(
        self,
        output_dir: Path = None,
        tier1_concurrency: int = 5,
        tier2_concurrency: int = 2,
    ):
        self.output_dir = output_dir or Path("C:/Users/User/vex-corpus/output")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.client = OllamaClient()
        self.dispatcher = TaskDispatcher(client=self.client)

        self.tier1_concurrency = tier1_concurrency
        self.tier2_concurrency = tier2_concurrency

        self.samples: dict[str, VEXSample] = {}
        self.processed_hashes: set[str] = set()

        # Callbacks
        self._on_progress: Callable[[str, int, int], None] | None = None
        self._on_sample_complete: Callable[[VEXSample], None] | None = None
        self._on_error: Callable[[str, Exception], None] | None = None

    def on_progress(self, callback: Callable[[str, int, int], None]):
        """Register progress callback (stage, current, total)."""
        self._on_progress = callback

    def on_sample_complete(self, callback: Callable[[VEXSample], None]):
        """Register sample completion callback."""
        self._on_sample_complete = callback

    def on_error(self, callback: Callable[[str, Exception], None]):
        """Register error callback."""
        self._on_error = callback

    def _report_progress(self, stage: str, current: int, total: int):
        if self._on_progress:
            self._on_progress(stage, current, total)

    async def ingest_file(self, file_path: Path) -> list[VEXSample]:
        """Ingest VEX samples from a file."""
        samples = VEXParser.extract_from_file(file_path)

        # Deduplicate
        new_samples = []
        for sample in samples:
            if sample.hash not in self.processed_hashes:
                self.samples[sample.id] = sample
                self.processed_hashes.add(sample.hash)
                new_samples.append(sample)

        return new_samples

    async def ingest_directory(self, dir_path: Path, recursive: bool = True) -> list[VEXSample]:
        """Ingest all VEX files from a directory."""
        all_samples = []

        pattern = "**/*" if recursive else "*"
        extensions = {'.vex', '.vfl', '.h', '.md', '.txt', '.json'}

        files = [f for f in dir_path.glob(pattern) if f.suffix in extensions]

        for i, file_path in enumerate(files):
            self._report_progress("Ingesting files", i + 1, len(files))
            try:
                samples = await self.ingest_file(file_path)
                all_samples.extend(samples)
            except Exception as e:
                if self._on_error:
                    self._on_error(str(file_path), e)

        return all_samples

    async def ingest_code(self, code: str, source: str = "direct_input") -> VEXSample:
        """Ingest a single VEX code string."""
        sample = VEXSample(id="", code=code, source_file=source)

        if sample.hash not in self.processed_hashes:
            self.samples[sample.id] = sample
            self.processed_hashes.add(sample.hash)

        return sample

    async def process_tier1(self, samples: list[VEXSample]) -> list[VEXSample]:
        """Run Tier 1 classification tasks on samples."""
        tier1_tasks = [
            "classify_wrangle",
            "extract_attributes",
            "extract_functions",
            "detect_bugs",
            "estimate_complexity",
            "classify_topic",
        ]

        total = len(samples) * len(tier1_tasks)
        completed = 0

        # Semaphore to limit concurrent Ollama requests (prevents timeouts)
        sem = asyncio.Semaphore(2)

        async def run_with_limit(task_type, code, task_id):
            async with sem:
                return await self.dispatcher.dispatch(task_type, {"code": code}, task_id=task_id)

        for sample in samples:
            sample.stage = ProcessingStage.TIER1_CLASSIFICATION

            # Run tier1 tasks with limited concurrency
            tasks = []
            for task_type in tier1_tasks:
                tasks.append(run_with_limit(
                    task_type,
                    sample.code,
                    f"{sample.id}_{task_type}"
                ))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results
            for task_type, result in zip(tier1_tasks, results):
                completed += 1
                self._report_progress("Tier 1 Classification", completed, total)

                if isinstance(result, Exception):
                    continue

                if result.status != TaskStatus.SUCCESS:
                    continue

                r = result.result

                if task_type == "classify_wrangle":
                    sample.context = r.get("context", "unknown")
                    sample.context_confidence = result.confidence
                elif task_type == "extract_attributes":
                    sample.attributes_read = r.get("reads", [])
                    sample.attributes_written = r.get("writes", [])
                    sample.channels = r.get("channels", [])
                elif task_type == "extract_functions":
                    sample.functions = r.get("functions", [])
                elif task_type == "detect_bugs":
                    sample.bugs = r.get("bugs", [])
                elif task_type == "estimate_complexity":
                    sample.complexity = r.get("complexity", "")
                elif task_type == "classify_topic":
                    sample.topic = r.get("primary_topic", "")

            # Check for review flags
            if sample.context_confidence < 0.6:
                sample.flagged_for_review = True
                sample.review_reason = f"Low context confidence: {sample.context_confidence:.2f}"

        return samples

    async def process_tier2(self, samples: list[VEXSample]) -> list[VEXSample]:
        """Run Tier 2 generation tasks on samples."""
        tier2_tasks = [
            "generate_prompt",
            "generate_explanation",
            "rate_difficulty",
        ]

        total = len(samples) * len(tier2_tasks)
        completed = 0

        for sample in samples:
            sample.stage = ProcessingStage.TIER2_GENERATION

            # Run tier2 tasks (sequentially to avoid overload)
            for task_type in tier2_tasks:
                completed += 1
                self._report_progress("Tier 2 Generation", completed, total)

                try:
                    input_data = {"code": sample.code}
                    if task_type == "generate_explanation":
                        input_data["audience"] = "intermediate"
                        input_data["task"] = sample.topic or "VEX operation"

                    result = await self.dispatcher.dispatch(
                        task_type,
                        input_data,
                        task_id=f"{sample.id}_{task_type}"
                    )

                    if result.status != TaskStatus.SUCCESS:
                        continue

                    r = result.result

                    if task_type == "generate_prompt":
                        sample.prompt = r.get("prompt", "")
                        sample.alternative_prompts = r.get("alternative_phrasings", [])
                    elif task_type == "generate_explanation":
                        sample.explanation = r.get("explanation", "")
                    elif task_type == "rate_difficulty":
                        sample.difficulty = r.get("difficulty", 0)

                except Exception as e:
                    if self._on_error:
                        self._on_error(f"{sample.id}_{task_type}", e)

            sample.stage = ProcessingStage.COMPLETE

            if self._on_sample_complete:
                self._on_sample_complete(sample)

        return samples

    async def process_all(self, samples: list[VEXSample] = None) -> list[VEXSample]:
        """Process all samples through the full pipeline."""
        if samples is None:
            samples = list(self.samples.values())

        # Filter to only pending samples
        pending = [s for s in samples if s.stage == ProcessingStage.PENDING]

        if not pending:
            return []

        # Tier 1
        await self.process_tier1(pending)

        # Tier 2 (only for samples that passed tier 1)
        tier2_candidates = [s for s in pending if not s.flagged_for_review]
        await self.process_tier2(tier2_candidates)

        return pending

    def export_corpus(self, output_path: Path = None) -> Path:
        """Export processed corpus to JSON."""
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = self.output_dir / f"vex_corpus_{timestamp}.json"

        completed = [s for s in self.samples.values() if s.stage == ProcessingStage.COMPLETE]

        corpus = {
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "total_samples": len(completed),
                "flagged_for_review": sum(1 for s in completed if s.flagged_for_review),
            },
            "samples": [s.to_dict() for s in completed],
        }

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(corpus, f, indent=2, ensure_ascii=False)

        return output_path

    def export_training_data(self, output_path: Path = None) -> Path:
        """Export as training pairs (prompt, completion)."""
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = self.output_dir / f"training_data_{timestamp}.jsonl"

        with open(output_path, 'w', encoding='utf-8') as f:
            for sample in self.samples.values():
                pair = sample.to_training_pair()
                if pair:
                    f.write(json.dumps(pair, ensure_ascii=False) + '\n')

        return output_path

    def get_stats(self) -> dict:
        """Get processing statistics."""
        samples = list(self.samples.values())

        by_stage = {}
        for stage in ProcessingStage:
            by_stage[stage.value] = sum(1 for s in samples if s.stage == stage)

        by_context = {}
        for s in samples:
            ctx = s.context or "unknown"
            by_context[ctx] = by_context.get(ctx, 0) + 1

        by_topic = {}
        for s in samples:
            topic = s.topic or "unknown"
            by_topic[topic] = by_topic.get(topic, 0) + 1

        return {
            "total": len(samples),
            "by_stage": by_stage,
            "by_context": by_context,
            "by_topic": by_topic,
            "flagged_for_review": sum(1 for s in samples if s.flagged_for_review),
            "with_prompts": sum(1 for s in samples if s.prompt),
        }
