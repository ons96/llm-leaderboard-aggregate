from __future__ import annotations

import json
from typing import Any

import requests

from ..util import ValidationResult, coerce_float, coerce_int
from .types import SourceProviderRecord


LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"


def fetch_litellm_raw(timeout_s: int = 30) -> bytes:
    resp = requests.get(LITELLM_URL, timeout=timeout_s)
    resp.raise_for_status()
    return resp.content


def validate_litellm_raw(data: bytes) -> ValidationResult:
    # LiteLLM list is large (thousands).
    from ..cache import validate_json_payload_bytes

    return validate_json_payload_bytes(data, min_rows=500, row_count_hint="litellm models dict length")


def parse_litellm_records(raw_json: Any) -> list[SourceProviderRecord]:
    # Root is dict {model_id: {...}}
    if not isinstance(raw_json, dict):
        return []
    out: list[SourceProviderRecord] = []
    for model_id, v in raw_json.items():
        if not model_id or not isinstance(v, dict):
            continue
        provider = v.get("litellm_provider") or v.get("provider") or None
        mode = v.get("mode") or None
        out.append(
            SourceProviderRecord(
                source="litellm",
                provider_name=str(provider).lower() if provider else None,
                provider_model_id=str(model_id),
                model_display_name=None,
                developer=None,
                release_date=None,
                context_window_tokens=coerce_int(v.get("max_tokens") or v.get("context_window") or v.get("max_context_length")),
                mode=str(mode).lower() if mode else None,
                input_cost_per_token=coerce_float(v.get("input_cost_per_token")),
                output_cost_per_token=coerce_float(v.get("output_cost_per_token")),
                is_free_tier=None,
            )
        )
    return out

