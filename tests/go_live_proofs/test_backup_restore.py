import os
import shutil
import sqlite3

import kuzu


def test_backup_and_restore():
    print("Starting Backup & Restore Test...")
    storage_dir = os.path.abspath("storage")
    backup_dir = os.path.abspath("/tmp/mesa_backup_test")

    sqlite_db = os.path.join(storage_dir, "mesa.db")
    kuzu_db = os.path.join(storage_dir, "kuzu_db")

    # 1. Count current data
    if not os.path.exists(sqlite_db):
        print("FAIL: No mesa.db found to backup!")
        return False

    conn = sqlite3.connect(sqlite_db)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM nodes")
    sqlite_memories_count = cur.fetchone()[0]
    conn.close()

    kuzu_nodes_count = 0
    if os.path.exists(kuzu_db):
        try:
            db = kuzu.Database(kuzu_db)
            k_conn = kuzu.Connection(db)
            res = k_conn.execute("MATCH (n:Entity) RETURN count(n)")
            while res.has_next():
                kuzu_nodes_count = res.get_next()[0]
        except Exception as e:
            print(f"Warning: Could not connect to KuzuDB: {e}")

    print(
        f"Current state: {sqlite_memories_count} memories, {kuzu_nodes_count} kuzu nodes"
    )

    # 2. Backup
    print(f"Backing up to {backup_dir}...")
    if os.path.exists(backup_dir):
        shutil.rmtree(backup_dir)
    os.makedirs(backup_dir)

    shutil.copy2(sqlite_db, os.path.join(backup_dir, "mesa.db"))
    if os.path.exists(kuzu_db):
        if os.path.isdir(kuzu_db):
            shutil.copytree(kuzu_db, os.path.join(backup_dir, "kuzu_db"))
        else:
            shutil.copy2(kuzu_db, os.path.join(backup_dir, "kuzu_db"))

    # 3. Simulate disaster (delete original)
    print("Simulating data loss...")
    os.remove(sqlite_db)
    if os.path.exists(kuzu_db):
        if os.path.isdir(kuzu_db):
            shutil.rmtree(kuzu_db)
        else:
            os.remove(kuzu_db)

    # 4. Restore
    print("Restoring from backup...")
    shutil.copy2(os.path.join(backup_dir, "mesa.db"), sqlite_db)
    if os.path.exists(os.path.join(backup_dir, "kuzu_db")):
        if os.path.isdir(os.path.join(backup_dir, "kuzu_db")):
            shutil.copytree(os.path.join(backup_dir, "kuzu_db"), kuzu_db)
        else:
            shutil.copy2(os.path.join(backup_dir, "kuzu_db"), kuzu_db)

    # 5. Verify
    conn = sqlite3.connect(sqlite_db)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM nodes")
    restored_memories_count = cur.fetchone()[0]
    conn.close()

    restored_nodes_count = 0
    if os.path.exists(kuzu_db):
        db = kuzu.Database(kuzu_db)
        k_conn = kuzu.Connection(db)
        res = k_conn.execute("MATCH (n:Entity) RETURN count(n)")
        while res.has_next():
            restored_nodes_count = res.get_next()[0]

    print(
        f"Restored state: {restored_memories_count} memories, {restored_nodes_count} kuzu nodes"
    )

    if (
        sqlite_memories_count == restored_memories_count
        and kuzu_nodes_count == restored_nodes_count
    ):
        print("PASS: Backup and Restore completed successfully!")
        return True
    else:
        print("FAIL: Data counts do not match!")
        return False


if __name__ == "__main__":
    test_backup_and_restore()
