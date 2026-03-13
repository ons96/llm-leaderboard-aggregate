from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..util import ValidationResult, atomic_copy, atomic_write_bytes, atomic_write_json, sha256_bytes, utc_now_iso


@dataclass(frozen=True)
class CachePaths:
    current_path: Path
    last_full_path: Path


def cache_paths(raw_dir: Path, source_key: str, ext: str) -> CachePaths:
    return CachePaths(
        current_path=raw_dir / f"{source_key}_current.{ext}",
        last_full_path=raw_dir / f"{source_key}_last_full.{ext}",
    )


def write_current(raw_dir: Path, source_key: str, ext: str, data: bytes) -> tuple[Path, str]:
    paths = cache_paths(raw_dir, source_key, ext)
    atomic_write_bytes(paths.current_path, data)
    return paths.current_path, sha256_bytes(data)


def promote_current(raw_dir: Path, source_key: str, ext: str) -> None:
    paths = cache_paths(raw_dir, source_key, ext)
    atomic_copy(paths.current_path, paths.last_full_path)


def validate_nonempty_bytes(data: bytes, *, min_bytes: int = 256) -> ValidationResult:
    if not data or len(data) < min_bytes:
        return ValidationResult(ok=False, row_count=0, error=f"payload too small: {len(data)} bytes < {min_bytes}")
    return ValidationResult(ok=True, row_count=1)


def manifest_entry(*, ok: bool, row_count: int, sha256: str | None, error: str | None = None) -> dict[str, Any]:
    return {
        "updated_at": utc_now_iso(),
        "ok": ok,
        "row_count": row_count,
        "sha256": sha256,
        "error": error,
    }


def update_manifest_file(manifest_path: Path, source_key: str, entry: dict[str, Any]) -> None:
    if manifest_path.exists():
        import json

        root = json.loads(manifest_path.read_text("utf-8"))
    else:
        root = {"updated_at": None, "sources": {}}
    root["updated_at"] = utc_now_iso()
    root.setdefault("sources", {})
    root["sources"][source_key] = entry
    atomic_write_json(manifest_path, root)

