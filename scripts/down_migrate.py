import csv
import logging
import os
import shutil

import kuzu

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)


def down_migrate(
    kuzu_path="storage/kuzu_db", backup_csv="storage/down_migration_backup.csv"
):
    if not os.path.exists(kuzu_path):
        logging.info("KuzuDB not found at %s. Nothing to down-migrate.", kuzu_path)
        return

    logging.info("Connecting to KuzuDB at %s...", kuzu_path)
    try:
        db = kuzu.Database(kuzu_path)
        conn = kuzu.Connection(db)

        # Backup nodes to CSV just in case
        logging.info("Backing up Entity nodes...")
        res = conn.execute("MATCH (n:Entity) RETURN n.id, n.type, n.name")
        assert isinstance(res, kuzu.QueryResult)
        with open(backup_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["id", "type", "name"])
            while res.has_next():
                writer.writerow(res.get_next())

        logging.info("Successfully backed up KuzuDB data to %s", backup_csv)
    except Exception as e:
        logging.error("Failed to connect or backup KuzuDB: %s", e)
        logging.info("Proceeding with rollback anyway...")

    logging.info("Rolling back (deleting KuzuDB)...")
    if os.path.isdir(kuzu_path):
        shutil.rmtree(kuzu_path)
    else:
        os.remove(kuzu_path)

    logging.info("Down-migration complete. KuzuDB removed.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Down-migrate from KuzuDB")
    parser.add_argument("--kuzu-db", default="storage/kuzu_db", help="Path to KuzuDB")
    args = parser.parse_args()

    down_migrate(args.kuzu_db)
