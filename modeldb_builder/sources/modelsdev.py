from __future__ import annotations

from typing import Any

import requests

from ..util import ValidationResult, coerce_float, coerce_int
from .types import SourceProviderRecord


MODELSDEV_URL = "https://models.dev/api.json"

_PER_MILLION = 1_000_000.0


def _per_million_to_per_token(x: float | None) -> float | None:
    if x is None:
        return None
    return x / _PER_MILLION


def fetch_modelsdev_raw(timeout_s: int = 60) -> bytes:
    resp = requests.get(MODELSDEV_URL, timeout=timeout_s)
    resp.raise_for_status()
    return resp.content


def validate_modelsdev_raw(data: bytes) -> ValidationResult:
    from ..cache import validate_json_payload_bytes

    return validate_json_payload_bytes(data, min_rows=10, row_count_hint="models.dev providers dict length")


def parse_modelsdev_records(raw_json: Any) -> list[SourceProviderRecord]:
    # Root is dict of providers: { provider_id: { id, api, models: { model_id: {...}} } }
    if not isinstance(raw_json, dict):
        return []

    out: list[SourceProviderRecord] = []
    for provider_id, provider_obj in raw_json.items():
        if not isinstance(provider_obj, dict):
            continue
        models = provider_obj.get("models")
        if not isinstance(models, dict):
            continue

        for model_id, m in models.items():
            if not model_id or not isinstance(m, dict):
                continue
            cost = m.get("cost") or {}
            limit = m.get("limit") or {}
            modalities = m.get("modalities") or {}
            mode = None
            if isinstance(modalities, dict):
                # Best-effort: if it can output text, treat as chat-like.
                out_mod = modalities.get("output")
                if isinstance(out_mod, list) and "text" in [str(x).lower() for x in out_mod]:
                    mode = "chat"

            # models.dev costs are "per 1M tokens" on the site; convert to per-token USD.
            in_cost = _per_million_to_per_token(coerce_float(cost.get("input")))
            out_cost = _per_million_to_per_token(coerce_float(cost.get("output")))
            is_free = None
            # Pricing notes: $0 or '-' may mean free but might be subscription-gated.
            if in_cost == 0.0 and out_cost == 0.0:
                is_free = 1

            out.append(
                SourceProviderRecord(
                    source="modelsdev",
                    provider_name=str(provider_id).lower(),
                    provider_model_id=str(m.get("id") or model_id),
                    model_display_name=m.get("name") or None,
                    developer=None,
                    release_date=m.get("release_date") if isinstance(m.get("release_date"), str) else None,
                    context_window_tokens=coerce_int(limit.get("context")),
                    mode=mode,
                    input_cost_per_token=in_cost,
                    output_cost_per_token=out_cost,
                    is_free_tier=is_free,
                )
            )
    return out
