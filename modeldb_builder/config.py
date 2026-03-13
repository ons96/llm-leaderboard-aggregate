from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    repo_root: Path

    @property
    def data_dir(self) -> Path:
        return self.repo_root / "data"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def db_dir(self) -> Path:
        return self.repo_root / "db"

    @property
    def scrape_manifest_path(self) -> Path:
        return self.raw_dir / "scrape_manifest.json"

    @property
    def scrape_state_path(self) -> Path:
        return self.raw_dir / "scrape_state.json"

    @property
    def models_db_path(self) -> Path:
        return self.db_dir / "models.db"

    @property
    def models_db_tmp_path(self) -> Path:
        return self.db_dir / "models.db.tmp"

    @property
    def models_unique_csv_path(self) -> Path:
        return self.db_dir / "models_unique.csv"

    @property
    def model_providers_csv_path(self) -> Path:
        return self.db_dir / "model_providers.csv"

    @property
    def models_unique_db_path(self) -> Path:
        return self.db_dir / "models_unique.db"

    @property
    def model_providers_db_path(self) -> Path:
        return self.db_dir / "model_providers.db"


def default_paths() -> Paths:
    # Resolve to repo root (this file lives at <root>/modeldb_builder/config.py)
    repo_root = Path(__file__).resolve().parent.parent
    return Paths(repo_root=repo_root)
