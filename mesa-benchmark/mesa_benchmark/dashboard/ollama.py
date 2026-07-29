from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx


def validate_ollama_url(value: str) -> str:
    """Accept only operator-allowlisted, credential-free Ollama roots."""
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Ollama adresi http:// veya https:// ile başlamalıdır")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Ollama adresinde kullanıcı adı veya parola kullanılamaz")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("Ollama adresi path, query veya fragment içeremez")
    hostname = parsed.hostname
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    normalized = urlunsplit((parsed.scheme, netloc, "", "", "")).rstrip("/")
    allowlist = {
        item.strip().rstrip("/")
        for item in os.environ.get(
            "MESA_BENCHMARK_OLLAMA_ALLOWLIST",
            "http://127.0.0.1:11434,http://localhost:11434",
        ).split(",")
        if item.strip()
    }
    if normalized not in allowlist:
        raise ValueError("Ollama adresi yönetici allowlist'inde değil")
    return normalized


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
