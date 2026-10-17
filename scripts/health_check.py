"""Check a running MESA API without opening local storage or loading models."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward an API credential to a redirect destination.
        return None


def check_api_health(base_url: str, api_key: str) -> bool:
    """Require readiness and authenticated health; never print server bodies/secrets."""
    try:
        parsed = urllib.parse.urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        # Accessing port also validates malformed or out-of-range port values.
        _ = parsed.port
    except ValueError:
        print(
            "FAIL: MESA_BASE_URL must be an HTTP(S) URL without credentials, query or fragment."
        )
        return False

    opener = urllib.request.build_opener(_NoRedirect())
    for path, expected in (("/health/init", "ready"), ("/health", "healthy")):
        headers = {}
        if path == "/health":
            if not api_key:
                print(
                    "FAIL: MESA_API_KEY is required for the authenticated /health check."
                )
                return False
            headers["X-API-Key"] = api_key
        try:
            request = urllib.request.Request(
                base_url.rstrip("/") + path, headers=headers
            )
            with opener.open(request, timeout=5) as response:
                if response.status != 200:
                    print(f"FAIL: {path} did not return HTTP 200.")
                    return False
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                print(
                    f"FAIL: {path} authorization failed (HTTP {error.code}); check MESA_API_KEY."
                )
            else:
                print(f"FAIL: {path} returned HTTP {error.code}.")
            return False
        except (OSError, ValueError):
            print(f"FAIL: {path} connection/timeout error or invalid JSON response.")
            return False
        if not isinstance(payload, dict) or payload.get("status") != expected:
            print(f"FAIL: {path} did not report {expected}.")
            return False
        print(f"OK: {path} reports {expected}.")
    return True


def main() -> int:
    base_url = os.environ.get("MESA_BASE_URL", "http://127.0.0.1:8000").strip()
    api_key = os.environ.get("MESA_API_KEY", "").strip()
    return 0 if check_api_health(base_url, api_key) else 1


if __name__ == "__main__":
    raise SystemExit(main())
