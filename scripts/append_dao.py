def append_to_dao():
    with open("mesa_storage/dao.py", "a") as f:
        f.write("""
    # ==================================================================
    # COST CONTROL (Daily Limits)
    # ==================================================================

    async def increment_and_check_daily_limit(self, agent_id: str, limit: int = 1000) -> bool:
        \"\"\"Increment the daily request counter for the agent and check if it exceeds the limit.
        Returns True if allowed, False if exceeded.
        \"\"\"
        from datetime import date
        today = date.today().isoformat()

        async with self._sql.transaction() as db:
            await db.execute(
                "INSERT INTO daily_limits (agent_id, date, request_count) "
                "VALUES (?, ?, 1) "
                "ON CONFLICT(agent_id, date) DO UPDATE SET request_count = request_count + 1",
                (agent_id, today)
            )

            async with db.execute(
                "SELECT request_count FROM daily_limits WHERE agent_id = ? AND date = ?",
                (agent_id, today)
            ) as cur:
                row = await cur.fetchone()

            await db.commit()

        if row and row[0] > limit:
            return False
        return True
""")
    print("Appended daily limit to dao.py")


if __name__ == "__main__":
    append_to_dao()
