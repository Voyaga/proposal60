import sqlite3
from pathlib import Path

DB_PATH = Path("gtj.db")

conn = sqlite3.connect(DB_PATH)

def add_column(sql):
    try:
        conn.execute(sql)
        print("OK:", sql)
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("SKIP (already exists)")
        else:
            raise

add_column("ALTER TABLE proposals ADD COLUMN accept_token TEXT;")
add_column("ALTER TABLE proposals ADD COLUMN accept_expires_at TEXT;")

conn.commit()
conn.close()
