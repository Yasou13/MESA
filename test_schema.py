import sqlite3

conn = sqlite3.connect("./storage/benchmark_mesa_sql.db")
cursor = conn.cursor()
cursor.execute("PRAGMA table_info(nodes);")
for row in cursor.fetchall():
    print(row)
