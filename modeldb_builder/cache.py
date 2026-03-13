from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .util import ValidationResult, atomic_copy, atomic_write_bytes, atomic_write_json, sha256_bytes, utc_now_iso


@dataclass(frozen=True)
class RawSourceCacheFiles:
    current_path: Path
    last_full_path: Path


class RawCache:
    def __init__(self, raw_dir: Path, manifest_path: Path, state_path: Path):
        self.raw_dir = raw_dir
        self.manifest_path = manifest_path
        self.state_path = state_path

    def _files_for(self, source_key: str, ext: str = "json") -> RawSourceCacheFiles:
        return RawSourceCacheFiles(
            current_path=self.raw_dir / f"{source_key}_current.{ext}",
            last_full_path=self.raw_dir / f"{source_key}_last_full.{ext}",
        )

    def load_last_full_json(self, source_key: str) -> Any | None:
        files = self._files_for(source_key, "json")
        if not files.last_full_path.exists():
            return None
        return json.loads(files.last_full_path.read_text("utf-8"))

    def write_current_json_bytes(self, source_key: str, data: bytes) -> str:
        files = self._files_for(source_key, "json")
        atomic_write_bytes(files.current_path, data)
        return sha256_bytes(data)

    def promote_current_to_last_full(self, source_key: str) -> None:
        files = self._files_for(source_key, "json")
        if not files.current_path.exists():
            raise FileNotFoundError(files.current_path)
        atomic_copy(files.current_path, files.last_full_path)

    def load_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {"updated_at": None, "sources": {}}
        return json.loads(self.manifest_path.read_text("utf-8"))

    def update_manifest(
        self,
        source_key: str,
        *,
        ok: bool,
        row_count: int,
        sha256: str | None,
        error: str | None = None,
    ) -> None:
        manifest = self.load_manifest()
        manifest["updated_at"] = utc_now_iso()
        manifest.setdefault("sources", {})
        manifest["sources"][source_key] = {
            "updated_at": utc_now_iso(),
            "ok": ok,
            "row_count": row_count,
            "sha256": sha256,
            "error": error,
        }
        atomic_write_json(self.manifest_path, manifest)

    def load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"updated_at": None, "sources": {}}
        return json.loads(self.state_path.read_text("utf-8"))

    def update_state(self, source_key: str, state: dict[str, Any]) -> None:
        root = self.load_state()
        root["updated_at"] = utc_now_iso()
        root.setdefault("sources", {})
        root["sources"][source_key] = state
        atomic_write_json(self.state_path, root)


def validate_json_payload_bytes(data: bytes, *, min_rows: int, row_count_hint: str) -> ValidationResult:
    try:
        obj = json.loads(data.decode("utf-8"))
    except Exception as e:
        return ValidationResult(ok=False, row_count=0, error=f"invalid json: {e}")

    row_count = 0
    if isinstance(obj, dict):
        row_count = len(obj)
    elif isinstance(obj, list):
        row_count = len(obj)
    else:
        return ValidationResult(ok=False, row_count=0, error=f"unexpected json root type: {type(obj).__name__}")

    if row_count < min_rows:
        return ValidationResult(
            ok=False,
            row_count=row_count,
            error=f"row count sanity check failed: {row_count} < {min_rows} ({row_count_hint})",
        )
    return ValidationResult(ok=True, row_count=row_count)

