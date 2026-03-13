from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from ..util import atomic_write_bytes, ensure_dir, utc_now_iso


def write_csv(path: Path, rows: list[dict[str, Any]], *, fieldnames: list[str]) -> None:
    ensure_dir(path.parent)
    out = []
    out.append(",".join(fieldnames))
    for r in rows:
        out.append(",".join(_csv_cell(r.get(k)) for k in fieldnames))
    atomic_write_bytes(path, ("\n".join(out) + "\n").encode("utf-8"))


def _csv_cell(v: Any) -> str:
    if v is None:
        s = ""
    else:
        s = str(v)
    output = []
    needs_quote = any(ch in s for ch in [",", "\"", "\n", "\r"])
    if needs_quote:
        s = s.replace("\"", "\"\"")
        return f"\"{s}\""
    return s


def write_json(path: Path, obj: Any) -> None:
    ensure_dir(path.parent)
    atomic_write_bytes(path, (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def now_metadata() -> dict[str, Any]:
    return {"generated_at": utc_now_iso()}

