from __future__ import annotations

import re


# Prefixes that are usually "provider routing" or "platform" prefixes rather than
# the actual model identifier. We strip exactly one leading segment if it matches.
_STRIP_PREFIXES = {
    "openai",
    "anthropic",
    "google",
    "xai",
    "cohere",
    "mistralai",
    "azure",
    "bedrock",
    "vertex",
    "vertex-ai",
    "openrouter",
    "groq",
    "together",
    "fireworks",
    "perplexity",
    "deepinfra",
    "replicate",
    "huggingface",
    "cloudflare",
    "databricks",
    "g4f",
}


def normalize_model_slug(model_id: str) -> str:
    """Best-effort canonicalization for deduping model IDs across sources.

    Goals:
    - Strip provider prefixes (openai/, openrouter/, etc.)
    - Normalize separators (_ . space -> -)
    - Preserve semantic version identifiers (dates, numeric versions, sizes)
    - Drop call-variant suffixes like ':free'
    """
    s = (model_id or "").strip().lower()
    if not s:
        return ""

    # Remove anything after ':' (OpenRouter uses ':free' and similar tags).
    if ":" in s:
        s = s.split(":", 1)[0]

    # Strip a single known prefix segment.
    if "/" in s:
        first, rest = s.split("/", 1)
        if first in _STRIP_PREFIXES:
            s = rest

    # Remaining '/' segments are part of the model identifier (common on models.dev / OpenRouter).
    # Canonical slugs should be path-safe, so fold them into '-'.
    s = s.replace("/", "-")

    # Normalize separators.
    s = re.sub(r"[_.\s]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    s = s.strip("-")

    return s


_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")


def base_key_without_versions(normalized_slug: str) -> str:
    """A coarser key used only for ambiguity detection."""
    s = normalized_slug
    s = _DATE_RE.sub("", s)
    s = s.replace("latest", "")
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s
