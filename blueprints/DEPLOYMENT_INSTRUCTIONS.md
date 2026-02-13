# VEX Corpus Ollama Deployment Instructions
## Quick Start Guide

---

## 1. Model Setup

### Verify Ollama Installation
```bash
# Check Ollama is running
ollama --version
curl http://localhost:11434/api/tags
```

### Pull Required Models
```bash
# Tier 1: Fast classification (4B, ~3GB VRAM)
ollama pull nemotron-mini

# Tier 2: Generation tasks (8B, ~6GB VRAM)  
ollama pull minitron

# Embedding model (768-dim, ~2GB VRAM)
ollama pull nomic-embed-text

# Fallback options if Nemotron unavailable
ollama pull qwen2.5-coder:7b
ollama pull deepseek-coder:6.7b
```

### Verify Models Loaded
```bash
ollama list
# Should show nemotron-mini, minitron, nomic-embed-text
```

---

## 2. Blueprint Deployment

### Option A: System Prompt Injection
Load blueprint as system prompt for each model:

```python
import httpx

BLUEPRINT = open("OLLAMA_PROCESSING_BLUEPRINT.md").read()

async def process_task(task_type: str, input_data: dict):
    response = await httpx.AsyncClient().post(
        "http://localhost:11434/api/generate",
        json={
            "model": "nemotron-mini",  # or minitron for Tier 2
            "system": BLUEPRINT,
            "prompt": json.dumps({
                "task": task_type,
                "input": input_data
            }),
            "format": "json",
            "options": {
                "temperature": 0.1,
                "num_predict": 1024
            }
        }
    )
    return response.json()
```

### Option B: Modelfile with Embedded Blueprint
Create custom model with blueprint baked in:

```dockerfile
# Save as Modelfile.vex-processor
FROM nemotron-mini

SYSTEM """
[PASTE FULL BLUEPRINT HERE]
"""

PARAMETER temperature 0.1
PARAMETER top_p 0.9
```

```bash
# Create custom model
ollama create vex-processor-tier1 -f Modelfile.vex-processor

# Test it
ollama run vex-processor-tier1 '{"task": "classify_wrangle", "input": {"code": "@P += @N;"}}'
```

### Option C: RAG-Augmented (Recommended for Updates)
Store blueprint sections in vector DB, retrieve relevant sections per task:

```python
# Chunk blueprint by section
sections = parse_blueprint_sections("OLLAMA_PROCESSING_BLUEPRINT.md")

# Embed and index
for section in sections:
    embedding = ollama_embed("nomic-embed-text", section.content)
    vector_db.insert(section.id, embedding, section.content)

# At runtime, retrieve relevant sections
def build_context(task_type: str) -> str:
    query = f"VEX {task_type} task specification"
    relevant = vector_db.search(query, k=3)
    return "\n\n".join(r.content for r in relevant)
```

---

## 3. Task Routing

### Tier 1 Tasks → nemotron-mini
Fast, deterministic classification:

```python
TIER1_TASKS = {
    "classify_wrangle",
    "extract_attributes", 
    "extract_functions",
    "detect_bugs",
    "estimate_complexity",
    "classify_topic"
}
```

### Tier 2 Tasks → minitron
Generative, reasoning-heavy:

```python
TIER2_TASKS = {
    "generate_prompt",
    "generate_explanation",
    "inject_bug",
    "paraphrase_prompt",
    "rate_difficulty",
    "infer_context"
}
```

### Router Implementation
```python
def route_task(task_type: str) -> str:
    if task_type in TIER1_TASKS:
        return "nemotron-mini"
    elif task_type in TIER2_TASKS:
        return "minitron"
    else:
        raise ValueError(f"Unknown task: {task_type}")
```

---

## 4. Request Format

### Single Task
```json
{
  "task": "classify_wrangle",
  "id": "sample_001",
  "input": {
    "code": "@P += curlnoise(@P * ch(\"freq\")) * ch(\"amp\");"
  }
}
```

### Batch Tasks
```json
{
  "task": "classify_wrangle",
  "batch": [
    {"id": "sample_001", "code": "@P += @N;"},
    {"id": "sample_002", "code": "int pts[] = primpoints(0, @primnum);"}
  ]
}
```

### Task with Context
```json
{
  "task": "generate_explanation",
  "id": "sample_001",
  "input": {
    "code": "...",
    "task": "Point relaxation",
    "context": {
      "wrangle_type": "point",
      "input_geo": "scattered points",
      "output_attrs": ["P"]
    },
    "audience": "intermediate"
  }
}
```

---

## 5. Response Handling

### Parse Response
```python
def parse_ollama_response(response: dict) -> dict:
    content = response.get("response", "")
    
    # Handle potential JSON in markdown blocks
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {
            "status": "error",
            "error_type": "parse_error",
            "raw_response": content
        }
```

### Validate Output
```python
def validate_result(task_type: str, result: dict) -> bool:
    if result.get("status") == "error":
        return False
    
    # Task-specific validation
    if task_type == "classify_wrangle":
        valid_contexts = {"point", "prim", "vertex", "detail", "unknown"}
        return result.get("result", {}).get("context") in valid_contexts
    
    # Add other task validations...
    return True
```

---

## 6. Consensus Protocol

For tasks requiring multiple model agreement:

```python
async def consensus_task(task_type: str, input_data: dict) -> dict:
    models = ["nemotron-mini", "qwen2.5-coder:7b", "deepseek-coder:6.7b"]
    
    results = await asyncio.gather(*[
        process_task(task_type, input_data, model=m)
        for m in models
    ])
    
    # Extract key field based on task
    if task_type == "classify_wrangle":
        votes = [r.get("result", {}).get("context") for r in results]
    elif task_type == "rate_difficulty":
        votes = [r.get("result", {}).get("difficulty") for r in results]
    
    # Calculate consensus
    from collections import Counter
    vote_counts = Counter(v for v in votes if v is not None)
    
    if not vote_counts:
        return {"status": "error", "error": "no valid votes"}
    
    winner, count = vote_counts.most_common(1)[0]
    consensus_score = count / len(models)
    
    # Return best matching result with consensus score
    for r in results:
        if r.get("result", {}).get("context") == winner:
            r["consensus_score"] = consensus_score
            return r
    
    return results[0]
```

---

## 7. Performance Tuning

### Memory Management
```bash
# Set Ollama to keep models loaded
export OLLAMA_KEEP_ALIVE=24h

# Limit concurrent models (your 24GB can run both)
export OLLAMA_MAX_LOADED_MODELS=2
```

### Parallel Processing
```python
# Run both tiers simultaneously
async def parallel_process(samples: list[dict]):
    tier1_tasks = []
    tier2_tasks = []
    
    for sample in samples:
        # Tier 1: Classification
        tier1_tasks.append(
            process_task("classify_wrangle", sample, model="nemotron-mini")
        )
        # Tier 2: Explanation (can run in parallel)
        tier2_tasks.append(
            process_task("generate_explanation", sample, model="minitron")
        )
    
    # Execute both tiers in parallel
    tier1_results, tier2_results = await asyncio.gather(
        asyncio.gather(*tier1_tasks),
        asyncio.gather(*tier2_tasks)
    )
    
    return tier1_results, tier2_results
```

### Batch Size Optimization
```python
# Optimal batch sizes based on model
BATCH_SIZES = {
    "nemotron-mini": 10,   # Fast, can batch more
    "minitron": 5,         # Slower, smaller batches
}
```

---

## 8. Quality Checkpoints

### Automatic Flagging
```python
def should_flag_for_review(result: dict) -> bool:
    # Low confidence
    if result.get("confidence", 1.0) < 0.6:
        return True
    
    # Consensus failure
    if result.get("consensus_score", 1.0) < 0.66:
        return True
    
    # Error status
    if result.get("status") in ("error", "uncertain"):
        return True
    
    return False
```

### Review Queue
```python
def process_with_review_queue(samples: list, review_queue: Queue):
    for sample in samples:
        result = process_task(sample["task"], sample)
        
        if should_flag_for_review(result):
            review_queue.put({
                "sample": sample,
                "result": result,
                "reason": determine_flag_reason(result)
            })
        else:
            save_result(result)
```

---

## 9. Monitoring

### Health Check
```python
async def health_check():
    async with httpx.AsyncClient() as client:
        response = await client.get("http://localhost:11434/api/tags")
        models = response.json().get("models", [])
        
        required = {"nemotron-mini", "minitron", "nomic-embed-text"}
        available = {m["name"].split(":")[0] for m in models}
        
        return {
            "status": "healthy" if required <= available else "degraded",
            "available_models": list(available),
            "missing_models": list(required - available)
        }
```

### Throughput Tracking
```python
from collections import deque
from time import time

class ThroughputTracker:
    def __init__(self, window_seconds=60):
        self.window = window_seconds
        self.timestamps = deque()
    
    def record(self):
        self.timestamps.append(time())
        self._cleanup()
    
    def _cleanup(self):
        cutoff = time() - self.window
        while self.timestamps and self.timestamps[0] < cutoff:
            self.timestamps.popleft()
    
    def rate(self) -> float:
        self._cleanup()
        return len(self.timestamps) / self.window * 60  # per minute
```

---

## 10. Fallback Chain

If primary models fail:

```python
FALLBACK_CHAINS = {
    "nemotron-mini": ["qwen2.5-coder:7b", "phi3:3.8b"],
    "minitron": ["mistral:7b", "llama3.2:8b"],
}

async def process_with_fallback(task: str, input_data: dict, primary: str):
    # Try primary
    result = await process_task(task, input_data, model=primary)
    if result.get("status") != "error":
        return result
    
    # Try fallbacks
    for fallback in FALLBACK_CHAINS.get(primary, []):
        result = await process_task(task, input_data, model=fallback)
        if result.get("status") != "error":
            result["fallback_used"] = fallback
            return result
    
    return {"status": "error", "error": "all models failed"}
```

---

## Quick Test Commands

```bash
# Test classification
curl http://localhost:11434/api/generate -d '{
  "model": "nemotron-mini",
  "prompt": "{\"task\": \"classify_wrangle\", \"input\": {\"code\": \"@P += @N;\"}}",
  "format": "json",
  "stream": false
}'

# Test generation
curl http://localhost:11434/api/generate -d '{
  "model": "minitron", 
  "prompt": "{\"task\": \"generate_prompt\", \"input\": {\"code\": \"@P += curlnoise(@P);\", \"task\": \"Curl noise displacement\"}}",
  "format": "json",
  "stream": false
}'

# Test embedding
curl http://localhost:11434/api/embeddings -d '{
  "model": "nomic-embed-text",
  "prompt": "VEX point cloud relaxation using pcopen"
}'
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Model not found | Run `ollama pull <model>` |
| JSON parse errors | Lower temperature, add format examples |
| Slow responses | Check VRAM usage, reduce batch size |
| Out of memory | Unload unused models: `ollama stop <model>` |
| Inconsistent output | Use consensus protocol |
| Missing fields | Add validation, flag for review |

---

*End of Instructions*
