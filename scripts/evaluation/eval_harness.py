"""Evaluation harness for VEX corpus retrieval quality.

Defines test queries with expected results and measures retrieval
precision, recall, NDCG, and MRR against the search index.

Usage:
    python scripts/evaluation/eval_harness.py                     # Run evaluation
    python scripts/evaluation/eval_harness.py --bm25-only         # BM25 only
    python scripts/evaluation/eval_harness.py --top-k 20          # Evaluate top-20
"""

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.search.build_index import (
    BM25Index,
    HybridSearcher,
    SemanticIndex,
    _embed_batch,
)

# ---------------------------------------------------------------------------
# Test queries with relevance judgments
# ---------------------------------------------------------------------------

# Each query has:
#   - query: natural language search query
#   - relevant_patterns: substrings that should appear in relevant chunk IDs or content
#   - required_functions: VEX functions that MUST appear in top results
#   - difficulty: expected difficulty level
#   - description: what a good result looks like

TEST_QUERIES = [
    {
        "id": "Q01",
        "query": "How do I color points based on their position?",
        "relevant_patterns": ["@Cd", "@P", "color", "position"],
        "required_functions": [],
        "expected_source": "joy-of-vex",
        "description": "Should return basic attribute mapping examples",
    },
    {
        "id": "Q02",
        "query": "point cloud search nearest neighbors VEX",
        "relevant_patterns": ["pcopen", "pcfind", "nearpoint", "neighbour"],
        "required_functions": ["pcopen", "pcfind", "nearpoints"],
        "expected_source": None,
        "description": "Should return point cloud examples and pcopen reference",
    },
    {
        "id": "Q03",
        "query": "how to use noise in VEX for procedural patterns",
        "relevant_patterns": ["noise", "procedural", "pattern", "frequency"],
        "required_functions": ["noise"],
        "expected_source": None,
        "description": "Should return noise usage examples",
    },
    {
        "id": "Q04",
        "query": "VEX solver SOP previous frame access",
        "relevant_patterns": ["solver", "prev_frame", "frame", "simulation"],
        "required_functions": [],
        "expected_source": None,
        "description": "Should return solver-related chunks",
    },
    {
        "id": "Q05",
        "query": "normalize vector direction length",
        "relevant_patterns": ["normalize", "length", "direction", "vector"],
        "required_functions": ["normalize", "length"],
        "expected_source": None,
        "description": "Should return vector math examples",
    },
    {
        "id": "Q06",
        "query": "fit remap values from one range to another",
        "relevant_patterns": ["fit", "remap", "range", "clamp"],
        "required_functions": ["fit"],
        "expected_source": None,
        "description": "Should return fit/fit01 usage examples",
    },
    {
        "id": "Q07",
        "query": "create and delete points in VEX",
        "relevant_patterns": ["addpoint", "removepoint", "create", "delete"],
        "required_functions": ["addpoint", "removepoint"],
        "expected_source": None,
        "description": "Should return geometry creation/deletion examples",
    },
    {
        "id": "Q08",
        "query": "channel reference parameter slider wrangle",
        "relevant_patterns": ["ch(", "chf(", "chi(", "parameter", "slider"],
        "required_functions": ["ch", "chf"],
        "expected_source": None,
        "description": "Should return channel function examples",
    },
    {
        "id": "Q09",
        "query": "matrix transform rotate scale VEX",
        "relevant_patterns": ["matrix", "rotate", "transform", "scale"],
        "required_functions": ["maketransform", "rotate"],
        "expected_source": None,
        "description": "Should return matrix/transform examples",
    },
    {
        "id": "Q10",
        "query": "ramp attribute color gradient",
        "relevant_patterns": ["ramp", "chramp", "gradient", "color"],
        "required_functions": ["chramp"],
        "expected_source": None,
        "description": "Should return ramp parameter examples",
    },
    {
        "id": "Q11",
        "query": "group points by attribute condition",
        "relevant_patterns": ["group", "setpointgroup", "inpointgroup", "condition"],
        "required_functions": ["setpointgroup"],
        "expected_source": None,
        "description": "Should return group manipulation examples",
    },
    {
        "id": "Q12",
        "query": "lerp smooth interpolation between values",
        "relevant_patterns": ["lerp", "smooth", "interpolat"],
        "required_functions": ["lerp", "smooth"],
        "expected_source": None,
        "description": "Should return interpolation examples",
    },
]


# ---------------------------------------------------------------------------
# Evaluation metrics
# ---------------------------------------------------------------------------

def _is_relevant(chunk_meta: dict, query_spec: dict) -> bool:
    """Check if a chunk is relevant to a query based on pattern matching."""
    patterns = query_spec.get("relevant_patterns", [])
    if not patterns:
        return False

    # Check content preview and title
    text = (
        chunk_meta.get("content_preview", "") + " " +
        chunk_meta.get("title", "")
    ).lower()

    # A chunk is relevant if it matches at least 2 patterns
    matches = sum(1 for p in patterns if p.lower() in text)
    return matches >= 2


def precision_at_k(relevant: list[bool], k: int) -> float:
    """Precision@K: fraction of top-K results that are relevant."""
    top_k = relevant[:k]
    if not top_k:
        return 0.0
    return sum(top_k) / len(top_k)


def recall_at_k(relevant: list[bool], total_relevant: int, k: int) -> float:
    """Recall@K: fraction of relevant docs found in top-K."""
    if total_relevant == 0:
        return 0.0
    top_k = relevant[:k]
    return sum(top_k) / total_relevant


def mrr(relevant: list[bool]) -> float:
    """Mean Reciprocal Rank: 1/rank of first relevant result."""
    for i, is_rel in enumerate(relevant):
        if is_rel:
            return 1.0 / (i + 1)
    return 0.0


def ndcg_at_k(relevant: list[bool], k: int) -> float:
    """Normalized Discounted Cumulative Gain at K."""
    dcg = sum(
        (1.0 if rel else 0.0) / math.log2(i + 2)
        for i, rel in enumerate(relevant[:k])
    )
    # Ideal DCG (all relevant at the top)
    n_rel = sum(relevant[:k])
    ideal_dcg = sum(1.0 / math.log2(i + 2) for i in range(n_rel))
    return dcg / ideal_dcg if ideal_dcg > 0 else 0.0


def has_required_functions(results: list[dict], chunk_index: dict, required: list[str]) -> bool:
    """Check if any top result contains the required VEX functions."""
    if not required:
        return True
    for r in results:
        chunk = chunk_index.get(r["id"], {})
        funcs = set(chunk.get("functions_referenced", []))
        if any(f in funcs for f in required):
            return True
    return False


# ---------------------------------------------------------------------------
# Evaluation runner
# ---------------------------------------------------------------------------

async def run_evaluation(
    index_dir: Path | None = None,
    corpus_path: Path | None = None,
    bm25_only: bool = False,
    top_k: int = 10,
) -> dict:
    """Run evaluation harness against the search index."""
    index_dir = index_dir or (PROJECT_ROOT / "output" / "index")
    corpus_path = corpus_path or (PROJECT_ROOT / "output" / "corpus" / "merged_corpus.jsonl")

    # Load index
    bm25_path = index_dir / "bm25.pkl"
    if not bm25_path.exists():
        print(f"Error: BM25 index not found at {bm25_path}. Run build_index.py first.")
        return {}

    bm25 = BM25Index.load(bm25_path)
    print(f"Loaded BM25 index: {bm25.n_docs} documents, {len(bm25.doc_freq):,} terms")

    semantic = None
    if not bm25_only:
        sem_path = index_dir / "semantic.npz"
        if sem_path.exists():
            semantic = SemanticIndex.load(sem_path)
            print(f"Loaded semantic index: {len(semantic.doc_ids)} documents")
        else:
            print("Semantic index not found, using BM25 only")

    searcher = HybridSearcher(bm25, semantic)

    # Load corpus for relevance checking
    chunk_index = {}
    with open(corpus_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunk = json.loads(line)
                chunk_index[chunk.get("id", "")] = chunk

    # Load metadata
    meta_path = index_dir / "chunk_meta.json"
    chunk_meta = {}
    if meta_path.exists():
        with open(meta_path, encoding="utf-8") as f:
            chunk_meta = json.load(f)

    # Run queries
    print(f"\n{'='*60}")
    print(f" EVALUATION: {len(TEST_QUERIES)} queries, top-{top_k}")
    print(f"{'='*60}\n")

    all_metrics = []

    for q in TEST_QUERIES:
        results = await searcher.search(q["query"], top_k=top_k)

        # Check relevance of each result
        relevance = []
        for r in results:
            meta = chunk_meta.get(r["id"], {})
            is_rel = _is_relevant(meta, q)
            relevance.append(is_rel)

        # Compute metrics
        total_relevant = sum(
            1 for cid, meta in chunk_meta.items()
            if _is_relevant(meta, q)
        )

        p_at_5 = precision_at_k(relevance, 5)
        p_at_10 = precision_at_k(relevance, top_k)
        r_at_10 = recall_at_k(relevance, total_relevant, top_k)
        query_mrr = mrr(relevance)
        query_ndcg = ndcg_at_k(relevance, top_k)
        has_funcs = has_required_functions(results, chunk_index, q.get("required_functions", []))

        metrics = {
            "query_id": q["id"],
            "query": q["query"],
            "p@5": p_at_5,
            "p@10": p_at_10,
            "r@10": r_at_10,
            "mrr": query_mrr,
            "ndcg@10": query_ndcg,
            "has_required_functions": has_funcs,
            "total_relevant": total_relevant,
            "relevant_in_top10": sum(relevance[:top_k]),
        }
        all_metrics.append(metrics)

        # Print per-query results
        status = "PASS" if p_at_5 >= 0.4 and query_mrr > 0 else "WEAK" if query_mrr > 0 else "FAIL"
        func_status = "ok" if has_funcs else "MISSING" if q.get("required_functions") else "n/a"
        print(f"[{status}] {q['id']}: {q['query']}")
        print(f"  P@5={p_at_5:.2f}  P@10={p_at_10:.2f}  MRR={query_mrr:.2f}  "
              f"NDCG@10={query_ndcg:.2f}  funcs={func_status}")
        if results:
            top3 = results[:3]
            for r in top3:
                meta = chunk_meta.get(r["id"], {})
                rel = "+" if _is_relevant(meta, q) else "-"
                title = meta.get('title', '')[:60].encode('ascii', 'replace').decode()
                print(f"  {rel} {r['id']}: {title}")
        print()

    # Aggregate metrics
    avg_p5 = np.mean([m["p@5"] for m in all_metrics])
    avg_p10 = np.mean([m["p@10"] for m in all_metrics])
    avg_mrr = np.mean([m["mrr"] for m in all_metrics])
    avg_ndcg = np.mean([m["ndcg@10"] for m in all_metrics])
    func_rate = np.mean([m["has_required_functions"] for m in all_metrics])
    pass_rate = np.mean([1 if m["p@5"] >= 0.4 and m["mrr"] > 0 else 0 for m in all_metrics])

    print(f"{'='*60}")
    print(f" AGGREGATE RESULTS")
    print(f"{'='*60}")
    print(f"  Avg P@5:    {avg_p5:.3f}")
    print(f"  Avg P@10:   {avg_p10:.3f}")
    print(f"  Avg MRR:    {avg_mrr:.3f}")
    print(f"  Avg NDCG@10:{avg_ndcg:.3f}")
    print(f"  Func match: {func_rate:.1%}")
    print(f"  Pass rate:  {pass_rate:.1%}")
    print(f"{'='*60}")

    # Write results
    results_path = index_dir / "eval_results.json"
    report = {
        "config": {"top_k": top_k, "bm25_only": bm25_only, "n_queries": len(TEST_QUERIES)},
        "aggregate": {
            "avg_precision_at_5": round(avg_p5, 4),
            "avg_precision_at_10": round(avg_p10, 4),
            "avg_mrr": round(avg_mrr, 4),
            "avg_ndcg_at_10": round(avg_ndcg, 4),
            "function_match_rate": round(func_rate, 4),
            "pass_rate": round(pass_rate, 4),
        },
        "per_query": all_metrics,
    }
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, sort_keys=True)
    print(f"\n  Results saved to: {results_path}")

    return report


def main():
    parser = argparse.ArgumentParser(description="Evaluate VEX corpus search quality")
    parser.add_argument("--index-dir", type=Path, help="Directory containing index files")
    parser.add_argument("--corpus", type=Path, help="Corpus JSONL file")
    parser.add_argument("--bm25-only", action="store_true", help="Use BM25 only (skip semantic)")
    parser.add_argument("--top-k", type=int, default=10, help="Evaluate top-K results")
    args = parser.parse_args()

    asyncio.run(run_evaluation(
        index_dir=args.index_dir,
        corpus_path=args.corpus,
        bm25_only=args.bm25_only,
        top_k=args.top_k,
    ))


if __name__ == "__main__":
    main()
