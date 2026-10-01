#!/usr/bin/env python3
"""Inspect Chroma collection stats from the command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.vector_store import get_store_stats  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect Chroma vector store")
    parser.add_argument("--source", help="Filter by metadata.source")
    parser.add_argument("--limit", type=int, default=5, help="Sample rows to show")
    parser.add_argument("--json", action="store_true", help="Print raw JSON")
    args = parser.parse_args()

    stats = get_store_stats(source=args.source, limit=args.limit)
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return

    print(f"Collection:  {stats['collection']}")
    print(f"Persist dir: {stats['persist_dir']}")
    print(f"Chunk count: {stats['count']}")
    if stats["sources"]:
        print("\nBy source:")
        for src, n in sorted(stats["sources"].items(), key=lambda x: (-x[1], x[0])):
            print(f"  {src}: {n}")
    if stats["samples"]:
        print("\nSamples:")
        for row in stats["samples"]:
            print(f"  [{row['id']}] source={row['source']} chunk={row['chunk_index']}")
            print(f"    {row['preview']}")


if __name__ == "__main__":
    main()
