"""Hybrid search index builder for VEX corpus.

Builds a search index combining:
  - BM25 (keyword/term frequency) via rank_bm25
  - Semantic embeddings via nomic-embed-text through Ollama

The index enables hybrid retrieval for the Synapse RAG layer:
  query -> BM25 top-K + semantic top-K -> reciprocal rank fusion -> results

Usage:
    python scripts/search/build_index.py                    # Build full index
    python scripts/search/build_index.py --bm25-only        # BM25 only (no Ollama)
    python scripts/search/build_index.py --query "noise"    # Build + test query
    python scripts/search/build_index.py --dry-run
"""

import argparse
import asyncio
import json
import math
import pickle
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

INDEX_DIR = PROJECT_ROOT / "output" / "index"
EMBEDDING_MODEL = "nemotron-mini:latest"
EMBEDDING_DIM = 3072
OLLAMA_URL = "http://localhost:11434"
BATCH_SIZE = 32  # Embeddings per batch


# ---------------------------------------------------------------------------
# Text preparation
# ---------------------------------------------------------------------------

def _chunk_to_text(chunk: dict) -> str:
    """Convert a chunk to searchable text."""
    parts = []

    title = chunk.get("title", "")
    if title:
        parts.append(title)

    content = chunk.get("content", "")
    if content:
        parts.append(content)

    # Include code
    for cb in chunk.get("code_blocks", []):
        code = cb.get("code", "") if isinstance(cb, dict) else str(cb)
        if code:
            parts.append(code)

    if chunk.get("code"):
        parts.append(chunk["code"])

    # Include prompt and explanation if available
    prompt = chunk.get("prompt", "")
    if prompt:
        parts.append(prompt)

    explanation = chunk.get("explanation", "")
    if explanation:
        parts.append(explanation)

    return "\n".join(parts)


def _tokenize(text: str) -> list[str]:
    """Simple tokenizer for BM25."""
    text = text.lower()
    # Keep @ for attribute names, keep _ for function names
    tokens = re.findall(r'@?\w+', text)
    # Filter very short tokens but keep VEX attributes like @P, @N
    return [t for t in tokens if len(t) > 1 or t.startswith("@")]


# ---------------------------------------------------------------------------
# BM25 Index
# ---------------------------------------------------------------------------

class BM25Index:
    """BM25 ranking index."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_ids: list[str] = []
        self.doc_lengths: list[int] = []
        self.avg_dl: float = 0.0
        self.n_docs: int = 0
        # term -> {doc_idx: term_freq}
        self.inverted_index: dict[str, dict[int, int]] = {}
        # term -> doc_freq
        self.doc_freq: dict[str, int] = {}

    def build(self, doc_ids: list[str], tokenized_docs: list[list[str]]):
        """Build the index from tokenized documents."""
        self.doc_ids = doc_ids
        self.n_docs = len(doc_ids)
        self.doc_lengths = [len(doc) for doc in tokenized_docs]
        self.avg_dl = sum(self.doc_lengths) / max(self.n_docs, 1)

        for idx, tokens in enumerate(tokenized_docs):
            seen = set()
            for token in tokens:
                # Update inverted index
                if token not in self.inverted_index:
                    self.inverted_index[token] = {}
                if idx not in self.inverted_index[token]:
                    self.inverted_index[token][idx] = 0
                self.inverted_index[token][idx] += 1

                # Update doc freq
                if token not in seen:
                    self.doc_freq[token] = self.doc_freq.get(token, 0) + 1
                    seen.add(token)

    def search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        """Search the index. Returns [(doc_id, score), ...]."""
        query_tokens = _tokenize(query)
        scores = {}

        for token in query_tokens:
            if token not in self.inverted_index:
                continue

            # IDF
            df = self.doc_freq.get(token, 0)
            idf = math.log((self.n_docs - df + 0.5) / (df + 0.5) + 1.0)

            for doc_idx, tf in self.inverted_index[token].items():
                dl = self.doc_lengths[doc_idx]
                # BM25 score
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (1 - self.b + self.b * dl / self.avg_dl)
                score = idf * numerator / denominator

                if doc_idx not in scores:
                    scores[doc_idx] = 0.0
                scores[doc_idx] += score

        # Sort by score
        ranked = sorted(scores.items(), key=lambda x: -x[1])[:top_k]
        return [(self.doc_ids[idx], score) for idx, score in ranked]

    def save(self, path: Path):
        """Save index to disk."""
        with open(path, "wb") as f:
            pickle.dump({
                "k1": self.k1, "b": self.b,
                "doc_ids": self.doc_ids,
                "doc_lengths": self.doc_lengths,
                "avg_dl": self.avg_dl,
                "n_docs": self.n_docs,
                "inverted_index": self.inverted_index,
                "doc_freq": self.doc_freq,
            }, f)

    @classmethod
    def load(cls, path: Path) -> "BM25Index":
        """Load index from disk."""
        with open(path, "rb") as f:
            data = pickle.load(f)
        idx = cls(k1=data["k1"], b=data["b"])
        idx.doc_ids = data["doc_ids"]
        idx.doc_lengths = data["doc_lengths"]
        idx.avg_dl = data["avg_dl"]
        idx.n_docs = data["n_docs"]
        idx.inverted_index = data["inverted_index"]
        idx.doc_freq = data["doc_freq"]
        return idx


# ---------------------------------------------------------------------------
# Semantic (embedding) Index
# ---------------------------------------------------------------------------

async def _embed_batch(texts: list[str], model: str = EMBEDDING_MODEL) -> list[list[float]]:
    """Get embeddings from Ollama for a batch of texts."""
    embeddings = []
    async with httpx.AsyncClient(timeout=120.0) as client:
        for text in texts:
            # Truncate long texts
            if len(text) > 8000:
                text = text[:8000]
            response = await client.post(
                f"{OLLAMA_URL}/api/embed",
                json={"model": model, "input": text},
            )
            response.raise_for_status()
            data = response.json()
            emb = data.get("embeddings", [[]])[0]
            embeddings.append(emb)
    return embeddings


class SemanticIndex:
    """Simple brute-force semantic search using cosine similarity."""

    def __init__(self):
        self.doc_ids: list[str] = []
        self.embeddings: np.ndarray | None = None

    def build(self, doc_ids: list[str], embeddings: np.ndarray):
        """Build from precomputed embeddings."""
        self.doc_ids = doc_ids
        self.embeddings = embeddings
        # Normalize for cosine similarity
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        self.embeddings = embeddings / norms

    def search(self, query_embedding: np.ndarray, top_k: int = 20) -> list[tuple[str, float]]:
        """Search by cosine similarity."""
        if self.embeddings is None:
            return []
        # Normalize query
        norm = np.linalg.norm(query_embedding)
        if norm > 0:
            query_embedding = query_embedding / norm

        scores = self.embeddings @ query_embedding
        top_indices = np.argsort(-scores)[:top_k]
        return [(self.doc_ids[i], float(scores[i])) for i in top_indices]

    def save(self, path: Path):
        """Save to disk."""
        np.savez_compressed(
            path,
            doc_ids=np.array(self.doc_ids, dtype=object),
            embeddings=self.embeddings,
        )

    @classmethod
    def load(cls, path: Path) -> "SemanticIndex":
        """Load from disk."""
        data = np.load(path, allow_pickle=True)
        idx = cls()
        idx.doc_ids = data["doc_ids"].tolist()
        idx.embeddings = data["embeddings"]
        return idx


# ---------------------------------------------------------------------------
# Hybrid search
# ---------------------------------------------------------------------------

def reciprocal_rank_fusion(
    results_lists: list[list[tuple[str, float]]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Combine multiple ranked lists using Reciprocal Rank Fusion.

    RRF score = sum(1 / (k + rank)) across all lists.
    """
    scores: dict[str, float] = {}
    for results in results_lists:
        for rank, (doc_id, _) in enumerate(results):
            if doc_id not in scores:
                scores[doc_id] = 0.0
            scores[doc_id] += 1.0 / (k + rank + 1)

    ranked = sorted(scores.items(), key=lambda x: -x[1])
    return ranked


class HybridSearcher:
    """Hybrid BM25 + semantic search with RRF fusion."""

    def __init__(self, bm25: BM25Index, semantic: SemanticIndex | None = None):
        self.bm25 = bm25
        self.semantic = semantic

    async def search(
        self,
        query: str,
        top_k: int = 10,
        bm25_weight: float = 0.5,
    ) -> list[dict]:
        """Search using hybrid retrieval."""
        results_lists = []

        # BM25
        bm25_results = self.bm25.search(query, top_k=top_k * 3)
        results_lists.append(bm25_results)

        # Semantic (if available)
        if self.semantic and self.semantic.embeddings is not None:
            query_emb = await _embed_batch([query])
            if query_emb and query_emb[0]:
                query_vec = np.array(query_emb[0])
                sem_results = self.semantic.search(query_vec, top_k=top_k * 3)
                results_lists.append(sem_results)

        # Fuse
        fused = reciprocal_rank_fusion(results_lists)[:top_k]

        return [{"id": doc_id, "score": score, "rank": i + 1}
                for i, (doc_id, score) in enumerate(fused)]


# ---------------------------------------------------------------------------
# Index builder
# ---------------------------------------------------------------------------

async def build_index(
    input_path: Path | None = None,
    output_dir: Path | None = None,
    bm25_only: bool = False,
    dry_run: bool = False,
    test_query: str | None = None,
) -> dict:
    """Build the search index."""
    input_path = input_path or (PROJECT_ROOT / "output" / "corpus" / "merged_corpus.jsonl")
    output_dir = output_dir or INDEX_DIR

    if not input_path.exists():
        print(f"Error: {input_path} not found.")
        return {}

    # Load corpus
    chunks = []
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))

    print(f"Loaded {len(chunks)} chunks from {input_path}")

    # Prepare texts
    doc_ids = [c.get("id", f"chunk_{i}") for i, c in enumerate(chunks)]
    texts = [_chunk_to_text(c) for c in chunks]
    tokenized = [_tokenize(t) for t in texts]

    print(f"  Total tokens: {sum(len(t) for t in tokenized):,}")
    print(f"  Avg tokens/chunk: {sum(len(t) for t in tokenized) // len(tokenized)}")

    if dry_run:
        print(f"\n[dry-run] Would build:")
        print(f"  BM25 index: {len(doc_ids)} documents")
        if not bm25_only:
            print(f"  Semantic index: {len(doc_ids)} embeddings ({EMBEDDING_DIM}d)")
        return {"chunks": len(chunks)}

    output_dir.mkdir(parents=True, exist_ok=True)

    # Build BM25
    print(f"\nBuilding BM25 index...")
    t0 = time.time()
    bm25 = BM25Index()
    bm25.build(doc_ids, tokenized)
    bm25_path = output_dir / "bm25.pkl"
    bm25.save(bm25_path)
    print(f"  BM25 index saved: {bm25_path} ({bm25_path.stat().st_size / 1024:.0f} KB)")
    print(f"  Unique terms: {len(bm25.doc_freq):,}")
    print(f"  Build time: {time.time() - t0:.1f}s")

    # Build semantic index
    semantic = None
    if not bm25_only:
        print(f"\nBuilding semantic index ({EMBEDDING_MODEL})...")
        t0 = time.time()

        all_embeddings = []
        for batch_start in range(0, len(texts), BATCH_SIZE):
            batch = texts[batch_start:batch_start + BATCH_SIZE]
            batch_emb = await _embed_batch(batch)
            all_embeddings.extend(batch_emb)
            processed = min(batch_start + BATCH_SIZE, len(texts))
            elapsed = time.time() - t0
            rate = processed / elapsed if elapsed > 0 else 0
            remaining = (len(texts) - processed) / rate if rate > 0 else 0
            print(f"  [{processed}/{len(texts)}] {rate:.1f} chunks/s, ~{remaining:.0f}s remaining")

        embeddings_array = np.array(all_embeddings, dtype=np.float32)
        semantic = SemanticIndex()
        semantic.build(doc_ids, embeddings_array)
        sem_path = output_dir / "semantic.npz"
        semantic.save(sem_path)
        print(f"  Semantic index saved: {sem_path} ({sem_path.stat().st_size / 1024:.0f} KB)")
        print(f"  Embedding dim: {embeddings_array.shape[1]}")
        print(f"  Build time: {time.time() - t0:.1f}s")

    # Save chunk metadata for result lookup
    meta = {c.get("id", ""): {
        "title": c.get("title", ""),
        "source_id": c.get("source_id", ""),
        "content_type": c.get("content_type", ""),
        "difficulty": c.get("difficulty", ""),
        "content_preview": c.get("content", "")[:200],
    } for c in chunks}
    meta_path = output_dir / "chunk_meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, sort_keys=True)
    print(f"\n  Metadata saved: {meta_path}")

    # Test query
    if test_query:
        print(f"\n{'='*50}")
        print(f"Test query: \"{test_query}\"")
        print(f"{'='*50}")

        searcher = HybridSearcher(bm25, semantic)
        results = await searcher.search(test_query, top_k=10)

        for r in results:
            chunk_meta = meta.get(r["id"], {})
            title = chunk_meta.get('title', 'no title').encode('ascii', 'replace').decode()
            preview = chunk_meta.get('content_preview', '')[:100].encode('ascii', 'replace').decode()
            print(f"  #{r['rank']} [{r['score']:.4f}] {r['id']}")
            print(f"     {title}")
            print(f"     {preview}")
            print()

    return {
        "chunks_indexed": len(chunks),
        "bm25_terms": len(bm25.doc_freq),
        "has_semantic": semantic is not None,
    }


def main():
    parser = argparse.ArgumentParser(description="Build search index for VEX corpus")
    parser.add_argument("--input", type=Path, help="Input JSONL corpus")
    parser.add_argument("--output", type=Path, help="Output directory for index files")
    parser.add_argument("--bm25-only", action="store_true", help="Skip semantic embeddings")
    parser.add_argument("--dry-run", action="store_true", help="Preview without building")
    parser.add_argument("--query", type=str, help="Test query after building")
    args = parser.parse_args()

    asyncio.run(build_index(
        input_path=args.input,
        output_dir=args.output,
        bm25_only=args.bm25_only,
        dry_run=args.dry_run,
        test_query=args.query,
    ))


if __name__ == "__main__":
    main()
