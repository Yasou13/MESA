"""WAVE-004-V explicit trusted-root queue contract."""
from pathlib import Path
import pytest
from mesa_memory.consolidation.loop import PersistentQueue

@pytest.mark.asyncio
async def test_explicit_trusted_root_accepts_contained_write_and_rejects_escape(tmp_path: Path):
    root=tmp_path / "trusted"; root.mkdir()
    queue=PersistentQueue(str(root / "dlq.jsonl"), trusted_root=str(root))
    await queue.aappend({"queue_id":"normal-1","agent_id":"tenant-a"})
    assert await queue.alen() == 1
    with pytest.raises(ValueError, match="escapes trusted root"):
        PersistentQueue(str(tmp_path / "outside.jsonl"), trusted_root=str(root))
    link=tmp_path / "root-link"; link.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="forbidden or symlinked"):
        PersistentQueue(str(link / "dlq.jsonl"), trusted_root=str(link))
