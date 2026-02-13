"""Task dispatcher with routing and batch processing for VEX corpus."""

import asyncio
from collections import Counter
from pathlib import Path
from typing import Any

from .client import OllamaClient
from .models import (
    BatchResult,
    TaskResult,
    TaskStatus,
    TaskType,
    TIER1_TASKS,
    TIER2_TASKS,
)


class TaskDispatcher:
    """Routes and dispatches VEX processing tasks to appropriate models."""

    def __init__(
        self,
        client: OllamaClient | None = None,
        config_path: Path | None = None,
    ):
        self.client = client or OllamaClient(config_path=config_path)
        self._config = self.client.config

    def get_tier(self, task_type: str | TaskType) -> int:
        """Determine which tier a task belongs to."""
        if isinstance(task_type, str):
            try:
                task_type = TaskType(task_type)
            except ValueError:
                return 1

        if task_type in TIER1_TASKS:
            return 1
        elif task_type in TIER2_TASKS:
            return 2
        return 1

    def get_model(self, task_type: str | TaskType) -> str:
        """Get the primary model for a task type."""
        tier = self.get_tier(task_type)
        return self.client.get_model_for_tier(tier).primary

    def should_use_consensus(self, task_type: str) -> bool:
        """Check if a task type requires consensus voting."""
        task_config = self._config.get("tasks", {}).get(task_type, {})
        return task_config.get("consensus", False)

    async def dispatch(
        self,
        task_type: str,
        input_data: dict[str, Any],
        task_id: str = "",
    ) -> TaskResult:
        """Dispatch a single task to the appropriate model."""
        if self.should_use_consensus(task_type):
            return await self.dispatch_with_consensus(task_type, input_data, task_id)
        return await self.client.process_task(task_type, input_data, task_id)

    async def dispatch_with_consensus(
        self,
        task_type: str,
        input_data: dict[str, Any],
        task_id: str = "",
    ) -> TaskResult:
        """Dispatch task to multiple models and use consensus voting."""
        tier = self.get_tier(task_type)
        model_config = self.client.get_model_for_tier(tier)

        # Get models to use for consensus
        models = [model_config.primary] + model_config.fallbacks[:2]

        # Run all models in parallel
        tasks = [
            self.client.process_task(task_type, input_data, task_id, model=m)
            for m in models
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter successful results
        valid_results: list[TaskResult] = []
        for r in results:
            if isinstance(r, TaskResult) and r.status == TaskStatus.SUCCESS:
                valid_results.append(r)

        if not valid_results:
            return TaskResult(
                task_id=task_id,
                status=TaskStatus.ERROR,
                result={"error_type": "consensus_failed", "error_message": "No valid results"},
            )

        # Extract vote key based on task type
        votes = []
        for r in valid_results:
            vote = self._extract_vote_key(task_type, r)
            if vote is not None:
                votes.append((vote, r))

        if not votes:
            return valid_results[0]

        # Count votes
        vote_counts = Counter(v[0] for v in votes)
        winner, count = vote_counts.most_common(1)[0]
        consensus_score = count / len(models)

        # Find best matching result
        for vote, result in votes:
            if vote == winner:
                result.result["consensus_score"] = consensus_score
                result.processing_notes = (
                    f"Consensus: {count}/{len(models)} models agreed"
                )
                return result

        return valid_results[0]

    def _extract_vote_key(self, task_type: str, result: TaskResult) -> Any:
        """Extract the key field to vote on based on task type."""
        r = result.result

        if task_type == "classify_wrangle":
            return r.get("context")
        elif task_type == "classify_topic":
            return r.get("primary_topic")
        elif task_type == "rate_difficulty":
            # Round to nearest 2 for fuzzy matching
            diff = r.get("difficulty")
            if diff is not None:
                return round(diff / 2) * 2
        elif task_type == "estimate_complexity":
            return r.get("complexity")
        elif task_type == "detect_bugs":
            bugs = r.get("bugs", [])
            return tuple(sorted(b.get("type", "") for b in bugs))

        return None

    async def dispatch_batch(
        self,
        task_type: str,
        items: list[dict[str, Any]],
        concurrency: int = 5,
    ) -> BatchResult:
        """Process a batch of items with concurrency control."""
        semaphore = asyncio.Semaphore(concurrency)

        async def process_item(item: dict[str, Any]) -> TaskResult:
            async with semaphore:
                item_id = item.get("id", "")
                code = item.get("code", "")
                return await self.dispatch(
                    task_type,
                    {"code": code, **{k: v for k, v in item.items() if k not in ("id", "code")}},
                    task_id=item_id,
                )

        results = await asyncio.gather(*[process_item(item) for item in items])

        # Compute stats
        success_count = sum(1 for r in results if r.status == TaskStatus.SUCCESS)
        error_count = sum(1 for r in results if r.status == TaskStatus.ERROR)
        confidences = [r.confidence for r in results if r.status == TaskStatus.SUCCESS]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        return BatchResult(
            results=list(results),
            batch_stats={
                "total": len(results),
                "success": success_count,
                "error": error_count,
                "avg_confidence": round(avg_confidence, 3),
            },
        )

    def should_flag_for_review(self, result: TaskResult) -> bool:
        """Check if a result should be flagged for human review."""
        quality_config = self._config.get("quality", {})
        confidence_threshold = quality_config.get("flag_confidence_below", 0.6)
        consensus_threshold = quality_config.get("flag_consensus_below", 0.66)

        # Low confidence
        if result.confidence < confidence_threshold:
            return True

        # Consensus failure
        consensus_score = result.result.get("consensus_score", 1.0)
        if consensus_score < consensus_threshold:
            return True

        # Error or uncertain status
        if result.status in (TaskStatus.ERROR, TaskStatus.UNCERTAIN):
            return True

        return False

    async def process_with_review(
        self,
        task_type: str,
        items: list[dict[str, Any]],
        concurrency: int = 5,
    ) -> tuple[list[TaskResult], list[TaskResult]]:
        """Process batch and separate results into passed and flagged."""
        batch_result = await self.dispatch_batch(task_type, items, concurrency)

        passed = []
        flagged = []

        for result in batch_result.results:
            if self.should_flag_for_review(result):
                flagged.append(result)
            else:
                passed.append(result)

        return passed, flagged
