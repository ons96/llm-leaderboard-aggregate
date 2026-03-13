from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_HEADERS = {
    "User-Agent": "llm-leaderboard-aggregate/benchmarks (+https://github.com/)",
    "Accept": "*/*",
}


@dataclass(frozen=True)
class HttpResult:
    url: str
    status: int
    content_type: str | None
    body: bytes


def http_get(url: str, *, timeout_s: int = 30, headers: dict[str, str] | None = None, retries: int = 2) -> HttpResult:
    last_err: Exception | None = None
    hdrs = dict(DEFAULT_HEADERS)
    if headers:
        hdrs.update(headers)
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                body = resp.read()
                ct = resp.headers.get("Content-Type")
                return HttpResult(url=url, status=getattr(resp, "status", 200), content_type=ct, body=body)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            last_err = e
            if attempt >= retries:
                raise
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"unreachable: {last_err}")


def json_loads_bytes(b: bytes) -> Any:
    return json.loads(b.decode("utf-8"))

