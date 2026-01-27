import sqlite3

conn = sqlite3.connect("gtj.db")
conn.row_factory = sqlite3.Row

rows = conn.execute(
    "SELECT id, status, business_name FROM proposals"
).fetchall()

for r in rows:
    print(dict(r))

conn.close()
