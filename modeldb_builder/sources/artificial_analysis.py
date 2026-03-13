from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

import cloudscraper
from bs4 import BeautifulSoup

from ..util import ValidationResult, coerce_float
from .types import ArtificialAnalysisMetrics


AA_MODELS_URL = "https://artificialanalysis.ai/leaderboards/models"


def fetch_artificial_analysis_html(timeout_s: int = 30) -> bytes:
    # First try cloudscraper (fast, works for non-JS content)
    scraper = cloudscraper.create_scraper()
    resp = scraper.get(AA_MODELS_URL, timeout=timeout_s)
    resp.raise_for_status()
    html = resp.content

    # Check if we got a table (non-JS version)
    if b"<table" in html:
        return html

    # If no table, try Playwright for JS-rendered content
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(AA_MODELS_URL, timeout=timeout_s * 1000)
            # Wait for table to appear (JS rendering)
            page.wait_for_selector("table", timeout=15000)
            html = page.content()
            browser.close()
        return html.encode("utf-8")
    except Exception:
        # Fall back to cloudscraper result even if no table
        return html


def validate_artificial_analysis_raw(data: bytes) -> ValidationResult:
    # Best-effort: any non-trivial HTML.
    if not data or len(data) < 10_000:
        return ValidationResult(ok=False, row_count=0, error="html too small or empty")
    return ValidationResult(ok=True, row_count=1)


def _find_table(soup: BeautifulSoup):
    table = soup.find("table")
    return table


def _parse_number(s: str) -> float | None:
    if not s:
        return None
    s = s.strip()
    m = re.search(r"(\d+(\.\d+)?)", s.replace(",", ""))
    if not m:
        return None
    return coerce_float(m.group(1))


def parse_artificial_analysis_metrics(
    html_bytes: bytes,
) -> list[ArtificialAnalysisMetrics]:
    """Best-effort scrape for provider-level performance metrics.

    This intentionally degrades gracefully: if the page layout changes, we return [].
    """
    soup = BeautifulSoup(html_bytes, "lxml")
    table = _find_table(soup)
    if table is None:
        return []

    # Parse header to map columns.
    headers = [th.get_text(" ", strip=True).lower() for th in table.find_all("th")]
    # Fall back if header isn't explicit; we'll still try by fixed positions.
    rows = table.find_all("tr")
    out: list[ArtificialAnalysisMetrics] = []
    for tr in rows[1:]:
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        cells = [td.get_text(" ", strip=True) for td in tds]

        # Heuristic: first cell usually model, second provider, then metrics columns.
        model_name = cells[0] if cells else None
        provider_name = cells[1] if len(cells) > 1 else None

        # Try to pick out numeric columns by header keywords if possible.
        tps = None
        ttft_ms = None
        quality = None
        if headers:
            for idx, h in enumerate(headers):
                if idx >= len(cells):
                    continue
                if "tokens" in h and ("sec" in h or "second" in h):
                    tps = _parse_number(cells[idx])
                if "ttft" in h or ("first" in h and "token" in h):
                    # Might be seconds; if it looks like "0.42s", convert to ms.
                    val = _parse_number(cells[idx])
                    if val is not None:
                        if "ms" in cells[idx].lower():
                            ttft_ms = val
                        else:
                            # assume seconds
                            ttft_ms = val * 1000.0
                if "quality" in h or "elo" in h or "score" in h:
                    quality = _parse_number(cells[idx])
        else:
            # Blind fallback: look at last 3 columns.
            if len(cells) >= 3:
                tps = _parse_number(cells[-3])
                ttft_ms = _parse_number(cells[-2])
                quality = _parse_number(cells[-1])

        out.append(
            ArtificialAnalysisMetrics(
                provider_name=(provider_name or "").strip().lower() or None,
                provider_model_id=None,
                model_display_name=(model_name or "").strip() or None,
                avg_tokens_per_second=tps,
                avg_ttft_ms=ttft_ms,
                quality_score=quality,
            )
        )

    return out
