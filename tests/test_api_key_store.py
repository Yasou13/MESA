"""Salted scrypt API key rotation and multi-principal contracts."""

import pytest

from mesa_memory.security.api_keys import APIKeyStore


@pytest.mark.asyncio
async def test_api_keys_are_key_id_addressed_hashed_and_rotatable(tmp_path) -> None:
    store = APIKeyStore(str(tmp_path / "rbac.db"))
    await store.initialize()
    assert not await store.has_active_key()
    first = await store.issue_key(principal_id="principal-a", principal_type="USER")
    assert await store.has_active_key()
    verified = await store.verify(first)
    assert verified is not None
    assert verified.principal_id == "principal-a"
    assert await store.verify("wrong." + first.partition(".")[2]) is None

    replacement = await store.rotate_key(verified.key_id)
    assert await store.verify(first) is None
    rotated = await store.verify(replacement)
    assert rotated is not None and rotated.principal_id == "principal-a"

    second = await store.issue_key(principal_id="principal-b", principal_type="SERVICE")
    assert (await store.verify(second)).principal_id == "principal-b"  # type: ignore[union-attr]
