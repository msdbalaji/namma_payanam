import csv
import os
from app.db import init_db, get_conn

def ingest_stops(csv_path=None):
    if csv_path is None:
        csv_path = os.path.join(os.path.dirname(__file__), "..", "gtfs_sample", "stops.csv")
    init_db()
    inserted = 0
    with get_conn() as conn:
        cur = conn.cursor()
        with open(csv_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                stop_id = row.get("stop_id") or row.get("id") or ""
                name = row.get("stop_name") or row.get("name") or "Unknown"
                try:
                    lat = float(row.get("stop_lat") or row.get("lat") or 0)
                    lon = float(row.get("stop_lon") or row.get("lon") or 0)
                except ValueError:
                    lat = 0.0
                    lon = 0.0
                desc = row.get("stop_desc") or None
                try:
                    cur.execute(
                        "INSERT OR IGNORE INTO stops (stop_id, name, lat, lon, desc) VALUES (?, ?, ?, ?, ?)",
                        (stop_id, name, lat, lon, desc)
                    )
                    # rowcount may be -1 for sqlite3 in some Python builds; we check lastrowid
                    if cur.lastrowid:
                        inserted += 1
                except Exception as e:
                    print("Insert error for", stop_id, e)
        conn.commit()
    print(f"Ingested {inserted} stops")

if __name__ == "__main__":
    ingest_stops()
