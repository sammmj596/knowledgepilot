"""
KnowledgePilot Golden Dataset Builder
================================

Interactive CLI that queries your existing vector store, shows you
candidate chunks for a question, and lets you mark which ones are actually
correct. Builds up golden_dataset.json incrementally so you can stop and
resume any time.

Usage:
    python knowledgepilot_golden_builder.py

At each prompt:
    - Type a question and hit enter to see candidate chunks
    - Enter the numbers of the chunks that correctly answer it (e.g. "1,3")
    - Optionally add a reference answer
    - Type nothing (just enter) at the question prompt to quit and save
"""

import json
import os
from typing import List, Dict, Any

# Requires the _search_context_raw() addition described in
# graph_py_addition.txt (returns raw Document objects instead of a
# formatted string, so we can pull out metadata for ids).
from app.graph import _search_context_raw

GOLDEN_DATASET_PATH = "golden_dataset.json"
CANDIDATES_PER_QUERY = 10


# Matches the metadata schema set in ingestion.py's ingest_text():
#   Document(page_content=c, metadata={**meta, "chunk_index": i})
# where meta already carries "source". Together these are a stable,
# unique per-chunk id (LangChain Documents don't expose .id directly).
def _chunk_id(doc) -> str:
    source = doc.metadata.get("source", "unknown")
    chunk_index = doc.metadata.get("chunk_index", "?")
    return f"{source}::{chunk_index}"


def query_candidates(query: str, n_results: int = CANDIDATES_PER_QUERY) -> List[Dict[str, Any]]:
    """
    Runs a raw similarity search (no top_k gating, no generation) so you can
    see the full candidate pool and hand-pick the correct ones. Uses a wide
    n_results here regardless of eval top_k, so you're always choosing from
    the full candidate pool.
    """
    docs = _search_context_raw(query, k=n_results)
    return [
        {"id": _chunk_id(d), "text": d.page_content, "distance": None}
        for d in docs
    ]


def load_existing_dataset(path: str = GOLDEN_DATASET_PATH) -> List[Dict[str, Any]]:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_dataset(dataset: List[Dict[str, Any]], path: str = GOLDEN_DATASET_PATH) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, indent=2, ensure_ascii=False)


def truncate(text: str, length: int = 200) -> str:
    text = text.replace("\n", " ").strip()
    return text if len(text) <= length else text[:length] + "..."


def prompt_for_selection(candidates: List[Dict[str, Any]]) -> List[str]:
    print(f"\nFound {len(candidates)} candidates:\n")
    for i, c in enumerate(candidates, start=1):
        dist = c.get("distance")
        dist_str = f"  (distance={dist:.3f})" if dist is not None else ""
        print(f"  [{i}] id={c['id']}{dist_str}")
        print(f"      {truncate(c['text'])}\n")

    raw = input("Which are correct? (comma-separated numbers, blank = none): ").strip()
    if not raw:
        return []

    indices = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(candidates):
                indices.append(idx)

    return [candidates[i]["id"] for i in indices]


def main():
    dataset = load_existing_dataset()
    print(f"Loaded {len(dataset)} existing golden examples from {GOLDEN_DATASET_PATH}\n")

    while True:
        query = input("\nEnter a question (blank to save & quit): ").strip()
        if not query:
            break

        candidates = query_candidates(query)
        if not candidates:
            print("No candidates returned — check your vector store connection in query_candidates().")
            continue

        relevant_ids = prompt_for_selection(candidates)
        if not relevant_ids:
            print("No chunks marked relevant — skipping this question.")
            continue

        reference_answer = input("Reference answer (optional, blank to skip): ").strip()

        dataset.append({
            "query": query,
            "relevant_chunk_ids": relevant_ids,
            "reference_answer": reference_answer,
        })

        save_dataset(dataset)
        print(f"Saved. Dataset now has {len(dataset)} examples.")

    print(f"\nDone. {len(dataset)} examples saved to {GOLDEN_DATASET_PATH}")


if __name__ == "__main__":
    main()