def fix_dao():
    path = "mesa_storage/dao.py"
    with open(path, "r") as f:
        content = f.read()

    # 1. Fix insert_memory
    old_insert = """        # ---- ATOMIC SAGA: SQLite + LanceDB (B-7 pattern) ----------
        # DO NOT commit SQLite until LanceDB succeeds.  On vector
        # failure, ROLLBACK SQLite to prevent orphaned relational records.
        async with self._sql.transaction() as db:
            # PHASE 1: Soft-delete conflicting nodes in SQLite
            if conflicting_node_ids:
                placeholders = ",".join("?" for _ in conflicting_node_ids)
                # Soft-delete nodes
                await db.execute(
                    f"UPDATE nodes SET invalid_at = CURRENT_TIMESTAMP "
                    f"WHERE id IN ({placeholders}) AND agent_id = ? "
                    f"AND invalid_at IS NULL",
                    (*conflicting_node_ids, agent_id),
                )
                # NOTE: Edge cascade removed — edges now live in KùzuDB
                # and are structurally bound to Entity nodes via MATCH.
                logger.info(
                    "SEMANTIC_CONFLICT_RESOLUTION | agent_id=%s new_node_id=%s "
                    "resolved_conflicts=%d soft_deleted=%s",
                    agent_id,
                    node_id,
                    len(conflicting_node_ids),
                    conflicting_node_ids,
                )

            # PHASE 2: Insert new node
            await db.execute(
                "INSERT INTO nodes "
                "(id, entity_name, type, content_payload, is_consolidated, created_at, "
                " agent_id, session_id) "
                "VALUES (?, ?, ?, ?, 0, ?, ?, ?)",
                (node_id, entity_name, node_type, content, now, agent_id, session_id),
            )

            # ---- LanceDB upsert (compensating rollback on fail) ------
            try:
                # Apply soft-delete in LanceDB for conflicts
                for cid in conflicting_node_ids:
                    await self._vec.soft_delete(cid, agent_id)

                if await self._is_lancedb_migrating(db):
                    import json

                    import numpy as np

                    vector_bytes = np.array(embedding, dtype=np.float32).tobytes()
                    wal_metadata = json.dumps(
                        {"node_id": node_id, "content_hash": content_hash}
                    )
                    wal_record_id = str(uuid.uuid4())

                    await db.execute(
                        "INSERT INTO lancedb_wal (id, agent_id, vector, metadata) "
                        "VALUES (?, ?, ?, ?)",
                        (wal_record_id, agent_id, vector_bytes, wal_metadata),
                    )
                    logger.info("UPSERT_QUEUED_IN_WAL | node_id=%s", node_id)
                else:
                    await self._vec.upsert(
                        node_id=node_id,
                        agent_id=agent_id,
                        embedding=embedding,
                        content_hash=content_hash,
                    )
            except Exception as vec_exc:
                await db.rollback()
                logger.error(
                    "INSERT_SAGA_ROLLBACK | agent_id=%s node_id=%s "
                    "vector_error=%s — SQL changes rolled back",
                    agent_id,
                    node_id,
                    vec_exc,
                )
                raise

            # Insert node into KuzuDB if graph provider is configured
            if self._graph is not None:
                try:
                    await self._graph.insert_node(
                        node_id=node_id,
                        name=entity_name,
                        agent_id=agent_id,
                    )
                except Exception as graph_exc:
                    logger.warning("Failed to insert node into KuzuDB: %s", graph_exc)

            # Both layers succeeded — commit the SQL transaction
            await db.commit()"""

    new_insert = """        # ---- ATOMIC SAGA: Secondary stores FIRST (fixes lock starvation) ----------
        is_migrating = await self._is_lancedb_migrating()

        try:
            if not is_migrating:
                for cid in conflicting_node_ids:
                    await self._vec.soft_delete(cid, agent_id)
                await self._vec.upsert(
                    node_id=node_id,
                    agent_id=agent_id,
                    embedding=embedding,
                    content_hash=content_hash,
                )
        except Exception as vec_exc:
            logger.error(
                "INSERT_SAGA_ROLLBACK | agent_id=%s node_id=%s "
                "vector_error=%s",
                agent_id,
                node_id,
                vec_exc,
            )
            raise

        if self._graph is not None:
            try:
                await self._graph.insert_node(
                    node_id=node_id,
                    name=entity_name,
                    agent_id=agent_id,
                )
            except Exception as graph_exc:
                logger.warning("Failed to insert node into KuzuDB: %s", graph_exc)

        # PHASE 2: Fast SQLite transaction
        async with self._sql.transaction() as db:
            if conflicting_node_ids:
                placeholders = ",".join("?" for _ in conflicting_node_ids)
                await db.execute(
                    f"UPDATE nodes SET invalid_at = CURRENT_TIMESTAMP "
                    f"WHERE id IN ({placeholders}) AND agent_id = ? "
                    f"AND invalid_at IS NULL",
                    (*conflicting_node_ids, agent_id),
                )
                logger.info(
                    "SEMANTIC_CONFLICT_RESOLUTION | agent_id=%s new_node_id=%s "
                    "resolved_conflicts=%d soft_deleted=%s",
                    agent_id,
                    node_id,
                    len(conflicting_node_ids),
                    conflicting_node_ids,
                )

            await db.execute(
                "INSERT INTO nodes "
                "(id, entity_name, type, content_payload, is_consolidated, created_at, "
                " agent_id, session_id) "
                "VALUES (?, ?, ?, ?, 0, ?, ?, ?)",
                (node_id, entity_name, node_type, content, now, agent_id, session_id),
            )

            if is_migrating:
                import json
                import numpy as np
                vector_bytes = np.array(embedding, dtype=np.float32).tobytes()
                wal_metadata = json.dumps({"node_id": node_id, "content_hash": content_hash})
                wal_record_id = str(uuid.uuid4())
                await db.execute(
                    "INSERT INTO lancedb_wal (id, agent_id, vector, metadata) "
                    "VALUES (?, ?, ?, ?)",
                    (wal_record_id, agent_id, vector_bytes, wal_metadata),
                )
                logger.info("UPSERT_QUEUED_IN_WAL | node_id=%s", node_id)

            await db.commit()"""

    content = content.replace(old_insert, new_insert)

    # 2. Fix bulk_insert_memory
    old_bulk = """        # ---- ATOMIC SAGA: SQLite + LanceDB (B-7 pattern) ----------
        async with self._sql.transaction() as db:
            await db.executemany(
                "INSERT INTO nodes "
                "(id, entity_name, type, content_payload, is_consolidated, created_at, "
                " agent_id, session_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                sql_rows,
            )

            # ---- LanceDB batch upsert (compensating rollback on fail)
            try:
                if await self._is_lancedb_migrating(db):
                    import json

                    import numpy as np

                    wal_records = []
                    for r in vec_rows:
                        vector_bytes = np.array(
                            r["embedding"], dtype=np.float32
                        ).tobytes()
                        wal_metadata = json.dumps(
                            {
                                "node_id": r["node_id"],
                                "content_hash": r.get("content_hash"),
                            }
                        )
                        wal_records.append(
                            (str(uuid.uuid4()), agent_id, vector_bytes, wal_metadata)
                        )

                    await db.executemany(
                        "INSERT INTO lancedb_wal (id, agent_id, vector, metadata) "
                        "VALUES (?, ?, ?, ?)",
                        wal_records,
                    )
                    logger.info(
                        "BULK_UPSERT_QUEUED_IN_WAL | count=%d", len(wal_records)
                    )
                else:
                    await self._vec.bulk_upsert(vec_rows)
            except Exception as vec_exc:
                await db.rollback()
                logger.error(
                    "BULK_INSERT_SAGA_ROLLBACK | agent_id=%s count=%d "
                    "vector_error=%s — SQL changes rolled back",
                    agent_id,
                    len(records),
                    vec_exc,
                )
                raise

            if self._graph is not None:
                try:
                    for rec, sql_row in zip(records, sql_rows):
                        await self._graph.insert_node(
                            node_id=sql_row[0],
                            name=sql_row[1],
                            agent_id=agent_id,
                        )
                except Exception as graph_exc:
                    logger.warning(
                        "Failed to bulk insert nodes into KuzuDB: %s", graph_exc
                    )

            # Both layers succeeded — commit
            await db.commit()"""

    new_bulk = """        # ---- ATOMIC SAGA: Secondary stores FIRST ----------
        is_migrating = await self._is_lancedb_migrating()

        try:
            if not is_migrating:
                await self._vec.bulk_upsert(vec_rows)
        except Exception as vec_exc:
            logger.error(
                "BULK_INSERT_SAGA_ROLLBACK | agent_id=%s count=%d "
                "vector_error=%s",
                agent_id,
                len(records),
                vec_exc,
            )
            raise

        if self._graph is not None:
            try:
                for rec, sql_row in zip(records, sql_rows):
                    await self._graph.insert_node(
                        node_id=sql_row[0],
                        name=sql_row[1],
                        agent_id=agent_id,
                    )
            except Exception as graph_exc:
                logger.warning("Failed to bulk insert nodes into KuzuDB: %s", graph_exc)

        # PHASE 2: Fast SQLite transaction
        async with self._sql.transaction() as db:
            await db.executemany(
                "INSERT INTO nodes "
                "(id, entity_name, type, content_payload, is_consolidated, created_at, "
                " agent_id, session_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                sql_rows,
            )

            if is_migrating:
                import json
                import numpy as np

                wal_records = []
                for r in vec_rows:
                    vector_bytes = np.array(
                        r["embedding"], dtype=np.float32
                    ).tobytes()
                    wal_metadata = json.dumps(
                        {
                            "node_id": r["node_id"],
                            "content_hash": r.get("content_hash"),
                        }
                    )
                    wal_records.append(
                        (str(uuid.uuid4()), agent_id, vector_bytes, wal_metadata)
                    )

                await db.executemany(
                    "INSERT INTO lancedb_wal (id, agent_id, vector, metadata) "
                    "VALUES (?, ?, ?, ?)",
                    wal_records,
                )
                logger.info(
                    "BULK_UPSERT_QUEUED_IN_WAL | count=%d", len(wal_records)
                )

            await db.commit()"""

    content = content.replace(old_bulk, new_bulk)

    # 3. Fix purge_memory
    old_purge = """        # ---- PHASE 1: RELATIONAL SOFT-DELETE FIRST (B-7 Saga Fix) -----
        # Execute SQLite soft-delete first inside a transaction. If the
        # subsequent vector layer fails, we can compensate by rolling
        # back the relational changes, preventing dangling SQL records
        # that reference live vector data.
        async with self._sql.transaction() as db:
            # ---- 1a. Soft-delete nodes: UPDATE SET invalid_at ----------
            #      CRITICAL: No DELETE — UPDATE only.
            #      RLS: WHERE agent_id = ? hardcoded.
            if scope == "session":
                update_sql = (
                    "UPDATE nodes "
                    "SET invalid_at = CURRENT_TIMESTAMP "
                    "WHERE agent_id = ? "
                    "  AND session_id = ? "
                    "  AND invalid_at IS NULL "
                    "  AND deleted_at IS NULL"
                )
                node_cursor = await db.execute(update_sql, (agent_id, session_id))
            else:
                update_sql = (
                    "UPDATE nodes "
                    "SET invalid_at = CURRENT_TIMESTAMP "
                    "WHERE agent_id = ? "
                    "  AND invalid_at IS NULL "
                    "  AND deleted_at IS NULL"
                )
                node_cursor = await db.execute(update_sql, (agent_id,))

            nodes_deleted = node_cursor.rowcount

            # NOTE: Edge cascade removed — edges now live in KùzuDB
            # and are structurally bound to Entity nodes via MATCH.

            # DO NOT commit yet — wait for vector layer success
            # ---- PHASE 2: VECTOR LAYER (compensating rollback on fail) -
            try:
                for nid in affected_ids:
                    await self._vec.soft_delete(nid, agent_id)
            except Exception as vec_exc:
                # Vector deletion failed — ROLLBACK the SQL transaction
                # to prevent dangling relational records.
                await db.rollback()
                logger.error(
                    "PURGE_SAGA_ROLLBACK | agent_id=%s "
                    "vector_error=%s — SQL changes rolled back",
                    agent_id,
                    vec_exc,
                )
                raise

            # Both layers succeeded — commit the SQL transaction
            await db.commit()"""

    new_purge = """        # ---- PHASE 1: SECONDARY STORE SOFT-DELETE FIRST ----------
        try:
            for nid in affected_ids:
                await self._vec.soft_delete(nid, agent_id)
        except Exception as vec_exc:
            logger.error(
                "PURGE_SAGA_ROLLBACK | agent_id=%s "
                "vector_error=%s",
                agent_id,
                vec_exc,
            )
            raise

        # ---- PHASE 2: Fast SQLite transaction
        async with self._sql.transaction() as db:
            if scope == "session":
                update_sql = (
                    "UPDATE nodes "
                    "SET invalid_at = CURRENT_TIMESTAMP "
                    "WHERE agent_id = ? "
                    "  AND session_id = ? "
                    "  AND invalid_at IS NULL "
                    "  AND deleted_at IS NULL"
                )
                node_cursor = await db.execute(update_sql, (agent_id, session_id))
            else:
                update_sql = (
                    "UPDATE nodes "
                    "SET invalid_at = CURRENT_TIMESTAMP "
                    "WHERE agent_id = ? "
                    "  AND invalid_at IS NULL "
                    "  AND deleted_at IS NULL"
                )
                node_cursor = await db.execute(update_sql, (agent_id,))

            nodes_deleted = node_cursor.rowcount
            await db.commit()"""

    content = content.replace(old_purge, new_purge)

    # 4. Fix update_entity_description
    old_update = """        async with self._sql.transaction() as db:
            await db.execute(
                "UPDATE nodes SET content_payload = ? "
                "WHERE id = ? AND agent_id = ? AND invalid_at IS NULL",
                (new_content, node_id, agent_id),
            )

            async def _vec_update():
                await self.vector_engine.upsert(
                    node_id=node_id,
                    agent_id=agent_id,
                    embedding=new_embedding,
                    content_hash=None,
                )

            await self._atomic_saga_commit(db, vector_func=_vec_update)"""

    new_update = """        try:
            await self.vector_engine.upsert(
                node_id=node_id,
                agent_id=agent_id,
                embedding=new_embedding,
                content_hash=None,
            )
        except Exception as vec_exc:
            logger.error("UPDATE_SAGA_ROLLBACK | agent_id=%s vector_error=%s", agent_id, vec_exc)
            raise

        async with self._sql.transaction() as db:
            await db.execute(
                "UPDATE nodes SET content_payload = ? "
                "WHERE id = ? AND agent_id = ? AND invalid_at IS NULL",
                (new_content, node_id, agent_id),
            )
            await db.commit()"""

    content = content.replace(old_update, new_update)

    # 5. Fix _invalidate_node
    old_inv = """        async with self._sql.transaction() as db:
            await db.execute(
                "UPDATE nodes SET invalid_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND agent_id = ? AND invalid_at IS NULL",
                (node_id, agent_id),
            )

            async def _graph_update():
                if self._graph:
                    await self._graph.execute_write(
                        "MATCH (n:Entity {id: $node_id, agent_id: $agent_id})"
                        "-[r:Observed]-() DELETE r",
                        {"node_id": node_id, "agent_id": agent_id},
                    )

            async def _vec_update():
                if self._vec:
                    await self._vec.hard_delete(node_id, agent_id)

            await self._atomic_saga_commit(
                db, vector_func=_vec_update, graph_func=_graph_update
            )"""

    new_inv = """        try:
            if self._vec:
                await self._vec.hard_delete(node_id, agent_id)
            if self._graph:
                await self._graph.execute_write(
                    "MATCH (n:Entity {id: $node_id, agent_id: $agent_id})"
                    "-[r:Observed]-() DELETE r",
                    {"node_id": node_id, "agent_id": agent_id},
                )
        except Exception as exc:
            logger.error("INVALIDATE_SAGA_ROLLBACK | agent_id=%s error=%s", agent_id, exc)
            raise

        async with self._sql.transaction() as db:
            await db.execute(
                "UPDATE nodes SET invalid_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND agent_id = ? AND invalid_at IS NULL",
                (node_id, agent_id),
            )
            await db.commit()"""

    content = content.replace(old_inv, new_inv)

    with open(path, "w") as f:
        f.write(content)

    print("DAO updated successfully.")


if __name__ == "__main__":
    fix_dao()
