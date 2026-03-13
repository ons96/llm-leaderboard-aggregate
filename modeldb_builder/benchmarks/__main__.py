from __future__ import annotations

import argparse
import json

from .pipeline import run


def main() -> int:
    ap = argparse.ArgumentParser(description="Benchmark aggregation pipeline (Phase 2)")
    ap.add_argument("--timeout", type=int, default=30, help="Per-request timeout seconds")
    ap.add_argument("--json", action="store_true", help="Print metadata JSON to stdout")
    args = ap.parse_args()

    meta = run(timeout_s=int(args.timeout))
    if args.json:
        print(json.dumps(meta, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

