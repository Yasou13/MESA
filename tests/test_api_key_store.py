"""Salted scrypt API key rotation and multi-principal contracts."""

import aiosqlite
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


@pytest.mark.asyncio
async def test_api_key_expiry_and_failed_rotation_preserve_the_active_key(
    tmp_path, monkeypatch
) -> None:
    store = APIKeyStore(str(tmp_path / "rbac.db"))
    await store.initialize()
    original = await store.issue_key(
        principal_id="principal-a", key_id="original", expires_in_seconds=60
    )
    collision = await store.issue_key(
        principal_id="principal-b", key_id="mk_collision", expires_in_seconds=60
    )
    assert await store.verify(original) is not None
    assert await store.verify(collision) is not None

    monkeypatch.setattr(
        "mesa_memory.security.api_keys.secrets.token_urlsafe",
        lambda _size: "collision",
    )
    with pytest.raises(aiosqlite.IntegrityError):
        await store.rotate_key("original")
    assert await store.verify(original) is not None

    async with aiosqlite.connect(store.policy_path) as db:
        await db.execute(
            "UPDATE api_keys SET expires_at = '1970-01-01T00:00:00+00:00' WHERE key_id = ?",
            ("original",),
        )
        await db.commit()
    assert await store.verify(original) is None
