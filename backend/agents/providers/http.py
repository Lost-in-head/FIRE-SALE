"""Small HTTP helper for the prospecting providers.

Live requests are refused while pytest is running so a test cannot
accidentally call Nominatim or Overpass.
"""

import os
import urllib.error
import urllib.request


def http_fetch(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict | None = None,
    timeout: float = 30,
) -> tuple[int, bytes]:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        raise RuntimeError(
            "Refusing live HTTP during tests. Inject a fetch callable."
        )
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers or {},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(1_000_000)
    except urllib.error.HTTPError as exc:
        payload = exc.read(200_000) if exc.fp else b""
        return exc.code, payload
