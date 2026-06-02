import kuzu

db = kuzu.Database(':memory:')
conn = kuzu.Connection(db)
conn.execute("CREATE NODE TABLE User(name STRING, age INT64, PRIMARY KEY (name))")
conn.execute("CREATE (u:User {name: 'Alice', age: 30})")
res = conn.execute("MATCH (u:User) RETURN u.name, u.age")
row = res.get_next()
print(type(row))
print(row)
