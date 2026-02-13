"""Async Ollama client for VEX corpus processing."""

import json
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel

from .models import TaskResult, TaskStatus


class ModelConfig(BaseModel):
    """Configuration for a model tier."""

    primary: str
    fallbacks: list[str] = []
    config: dict[str, Any] = {}


class OllamaClient:
    """Async client for Ollama API with VEX corpus processing support."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        config_path: Path | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.config = self._load_config(config_path)
        self._blueprint: str | None = None

    def _load_config(self, config_path: Path | None) -> dict[str, Any]:
        """Load configuration from YAML file."""
        if config_path is None:
            config_path = Path(__file__).parent.parent.parent / "config" / "models.yaml"

        if config_path.exists():
            with open(config_path) as f:
                return yaml.safe_load(f)
        return {}

    def load_blueprint(self, blueprint_path: Path | None = None) -> str:
        """Load the VEX processing blueprint as system prompt."""
        if blueprint_path is None:
            blueprint_path = (
                Path(__file__).parent.parent.parent
                / "blueprints"
                / "OLLAMA_PROCESSING_BLUEPRINT.md"
            )

        if blueprint_path.exists():
            self._blueprint = blueprint_path.read_text(encoding="utf-8")
        return self._blueprint or ""

    @property
    def blueprint(self) -> str:
        """Get the loaded blueprint, loading if necessary."""
        if self._blueprint is None:
            self.load_blueprint()
        return self._blueprint or ""

    def get_model_for_tier(self, tier: int) -> ModelConfig:
        """Get model configuration for the specified tier."""
        tier_key = f"tier{tier}"
        tier_config = self.config.get("models", {}).get(tier_key, {})
        return ModelConfig(
            primary=tier_config.get("primary", "nemotron-mini:latest"),
            fallbacks=tier_config.get("fallbacks", []),
            config=tier_config.get("config", {}),
        )

    async def health_check(self) -> dict[str, Any]:
        """Check Ollama server health and available models."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{self.base_url}/api/tags", timeout=10.0)
                response.raise_for_status()
                data = response.json()

                models = data.get("models", [])
                available = {m["name"].split(":")[0] for m in models}

                required = {"nemotron-mini", "nemotron"}
                missing = required - available

                return {
                    "status": "healthy" if not missing else "degraded",
                    "available_models": sorted(available),
                    "missing_models": sorted(missing),
                    "model_count": len(models),
                }
            except httpx.RequestError as e:
                return {
                    "status": "offline",
                    "error": str(e),
                    "available_models": [],
                    "missing_models": [],
                }

    async def generate(
        self,
        model: str,
        prompt: str,
        system: str | None = None,
        options: dict[str, Any] | None = None,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Send a generate request to Ollama."""
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }

        if system:
            payload["system"] = system

        if options:
            payload["options"] = options
            if "format" in options:
                payload["format"] = options.pop("format")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json()

    async def process_task(
        self,
        task_type: str,
        input_data: dict[str, Any],
        task_id: str = "",
        model: str | None = None,
        use_focused_prompt: bool = True,
    ) -> TaskResult:
        """Process a VEX task using the appropriate model."""
        from .models import TIER1_TASKS, TIER2_TASKS, TaskType
        from .prompts import get_task_prompt

        # Determine tier and model
        try:
            task_enum = TaskType(task_type)
        except ValueError:
            return TaskResult(
                task_id=task_id,
                status=TaskStatus.ERROR,
                result={"error_type": "unknown_task", "error_message": f"Unknown task: {task_type}"},
            )

        if task_enum in TIER1_TASKS:
            tier = 1
        elif task_enum in TIER2_TASKS:
            tier = 2
        else:
            tier = 1

        model_config = self.get_model_for_tier(tier)
        selected_model = model or model_config.primary

        # Get task config - timeouts now properly configured in models.yaml
        task_config = self.config.get("tasks", {}).get(task_type, {})
        timeout = task_config.get("timeout_seconds", 60) * 1.0

        # Build prompt - include the code directly for better parsing
        code = input_data.get("code", "")
        prompt = f"Task ID: {task_id}\n\nVEX Code:\n```vex\n{code}\n```\n\nAnalyze this VEX code and respond with JSON."

        # Get system prompt - use focused prompt for better results with smaller models
        if use_focused_prompt:
            system = get_task_prompt(task_type) or self.blueprint
        else:
            system = self.blueprint

        # Build options from model config
        options = dict(model_config.config)

        models_to_try = [selected_model] + model_config.fallbacks

        for i, model_name in enumerate(models_to_try):
            try:
                response = await self.generate(
                    model=model_name,
                    prompt=prompt,
                    system=system,
                    options=options,
                    timeout=timeout,
                )

                result = self._parse_response(response, task_id)
                result.model_used = model_name
                if i > 0:
                    result.fallback_used = model_name
                return result

            except httpx.TimeoutException:
                if i < len(models_to_try) - 1:
                    continue
                return TaskResult(
                    task_id=task_id,
                    status=TaskStatus.ERROR,
                    result={"error_type": "timeout", "error_message": "All models timed out"},
                    model_used=model_name,
                )
            except httpx.HTTPStatusError as e:
                if i < len(models_to_try) - 1:
                    continue
                return TaskResult(
                    task_id=task_id,
                    status=TaskStatus.ERROR,
                    result={"error_type": "http_error", "error_message": str(e)},
                    model_used=model_name,
                )
            except Exception as e:
                if i < len(models_to_try) - 1:
                    continue
                return TaskResult(
                    task_id=task_id,
                    status=TaskStatus.ERROR,
                    result={"error_type": "unknown_error", "error_message": str(e)},
                    model_used=model_name,
                )

        return TaskResult(
            task_id=task_id,
            status=TaskStatus.ERROR,
            result={"error_type": "all_models_failed", "error_message": "No models available"},
        )

    def _parse_response(self, response: dict[str, Any], task_id: str) -> TaskResult:
        """Parse Ollama response into TaskResult."""
        content = response.get("response", "")

        # Handle JSON in markdown blocks
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        content = content.strip()

        try:
            parsed = json.loads(content)

            # Extract fields from parsed response
            status_str = parsed.get("status", "success")
            try:
                status = TaskStatus(status_str)
            except ValueError:
                status = TaskStatus.SUCCESS

            return TaskResult(
                task_id=parsed.get("task_id", task_id),
                status=status,
                result=parsed.get("result", parsed),
                confidence=float(parsed.get("confidence", 0.0)),
                processing_notes=parsed.get("processing_notes", ""),
            )

        except json.JSONDecodeError:
            return TaskResult(
                task_id=task_id,
                status=TaskStatus.ERROR,
                result={
                    "error_type": "parse_error",
                    "error_message": "Failed to parse JSON response",
                    "raw_response": content[:500],
                },
            )

    async def list_models(self) -> list[dict[str, Any]]:
        """List all available Ollama models."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url}/api/tags", timeout=10.0)
            response.raise_for_status()
            return response.json().get("models", [])
