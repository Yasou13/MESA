from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx


def validate_ollama_url(value: str) -> str:
    """Accept only credential-free loopback/private-network Ollama roots."""
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Ollama adresi http:// veya https:// ile başlamalıdır")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Ollama adresinde kullanıcı adı veya parola kullanılamaz")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("Ollama adresi path, query veya fragment içeremez")
    hostname = parsed.hostname
    allowed = False
    try:
        address = ipaddress.ip_address(hostname)
        allowed = address.is_loopback or address.is_private
    except ValueError:
        if hostname.lower() == "localhost":
            allowed = True
        else:
            try:
                resolved = {
                    item[4][0]
                    for item in socket.getaddrinfo(
                        hostname, parsed.port or 11434, type=socket.SOCK_STREAM
                    )
                }
            except OSError as exc:
                raise ValueError("Ollama hostname çözümlenemedi") from exc
            allowed = bool(resolved) and all(
                (
                    (address := ipaddress.ip_address(item)).is_loopback
                    or address.is_private
                )
                for item in resolved
            )
    if not allowed:
        raise ValueError("Yalnız loopback veya özel LAN Ollama adresleri kabul edilir")
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return urlunsplit((parsed.scheme, netloc, "", "", "")).rstrip("/")


async def inspect_ollama(value: str, *, timeout_s: float = 4.0) -> dict[str, Any]:
    url = validate_ollama_url(value)
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        response = await client.get(f"{url}/api/tags")
        response.raise_for_status()
    models = [
        str(item.get("name") or item.get("model"))
        for item in response.json().get("models", [])
        if item.get("name") or item.get("model")
    ]
    return {"online": True, "url": url, "models": models}
