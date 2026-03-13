from __future__ import annotations

import argparse
import json
import sys

from .config import default_paths
from .pipeline import run_full_update


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="LLM Model Database Builder")
    p.add_argument("--json", action="store_true", help="Print summary as JSON")
    args = p.parse_args(argv)

    summary = run_full_update(default_paths())
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            f"updated_at={summary['updated_at']} unique_models={summary['unique_models']} provider_rows={summary['provider_rows']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

