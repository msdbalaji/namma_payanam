import sqlite3
import os
from contextlib import contextmanager

DB_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "stops.db")
DB_FILE = os.path.abspath(DB_FILE)

os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS stops (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stop_id TEXT UNIQUE,
        name TEXT,
        lat REAL,
        lon REAL,
        desc TEXT
    );
    """)
    conn.commit()
    conn.close()

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_FILE)
    try:
        yield conn
    finally:
        conn.close()
