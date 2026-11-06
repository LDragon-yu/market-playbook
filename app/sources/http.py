from __future__ import annotations

import json
import gzip
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class HTTPClientError(RuntimeError):
    pass


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    form: dict[str, Any] | None = None,
    timeout: int = 30,
    retries: int = 2,
) -> dict[str, Any]:
    request_headers = {"User-Agent": "market-playbook/0.3.1", "Accept-Encoding": "gzip", **(headers or {})}
    body: bytes | None = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    elif form is not None:
        body = urlencode(form).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = Request(url, data=body, headers=request_headers, method=method)
            with urlopen(request, timeout=timeout) as response:
                content = response.read()
                if response.headers.get("Content-Encoding", "" ).lower() == "gzip":
                    content = gzip.decompress(content)
                raw = content.decode("utf-8")
            return json.loads(raw)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.5 * (2**attempt))
    raise HTTPClientError(f"request failed: {url}: {last_error}")

