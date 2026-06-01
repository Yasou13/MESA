import json
import os
import sqlite3
import sys


def migrate_raw_logs(db_path: str):
    """
    Connects to the SQLite database, iterates through raw_logs,
    extracts agent_id from payload, and updates the agent_id column.
    """
    if not os.path.exists(db_path):
        print(f"Error: Database file not found at {db_path}")
        sys.exit(1)

    print(f"Connecting to database: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # Fetch all rows from raw_logs
        print("Fetching rows from raw_logs...")
        cursor.execute("SELECT id, payload FROM raw_logs")
        rows = cursor.fetchall()

        migrated_count = 0
        error_count = 0

        for row in rows:
            row_id = row["id"]
            payload_str = row["payload"]

            try:
                payload_data = json.loads(payload_str)
                # Default to '__unset__' if not found, matching schema defaults for other tables
                agent_id = payload_data.get("agent_id", "__unset__")

                # Execute an UPDATE statement to write the extracted agent_id
                cursor.execute(
                    "UPDATE raw_logs SET agent_id = ? WHERE id = ?",
                    (agent_id, row_id)
                )
                migrated_count += 1

            except json.JSONDecodeError:
                print(f"Error decoding JSON for row ID {row_id}. Skipping.")
                error_count += 1
                continue

        # Commit the transaction
        print("Committing transaction...")
        conn.commit()
        print(f"Migration successful. Migrated {migrated_count} rows. Errors: {error_count}.")

    except Exception as e:
        print("Error during migration. Rolling back transaction.")
        conn.rollback()
        raise e
    finally:
        conn.close()

if __name__ == "__main__":
    # Allow passing db path as an argument or using MESA_DB_PATH env var
    default_db = os.environ.get("MESA_DB_PATH", "mesa.db")
    target_db_path = sys.argv[1] if len(sys.argv) > 1 else default_db
    migrate_raw_logs(target_db_path)
