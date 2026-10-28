"""Shared D31 definition of a local model endpoint."""

from urllib.parse import urlparse


def is_local_endpoint(url: str) -> bool:
    """Allow only HTTP(S) endpoints on the installation's explicit local hosts."""
    try:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and parsed.hostname in {
            "localhost", "127.0.0.1", "::1", "host.docker.internal",
        }
    except ValueError:
        return False
