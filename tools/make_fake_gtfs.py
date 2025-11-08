# backend/tools/make_fake_gtfs.py
import csv, os, math
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(__file__))           # backend/
GTFS_DIR = os.path.join(ROOT, "gtfs")
SAMPLE_STOPS = os.path.join(ROOT, "gtfs_sample", "stops.csv")

os.makedirs(GTFS_DIR, exist_ok=True)

def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow(r)

def hhmmss(dt):
    return dt.strftime("%H:%M:%S")

def main():
    # Load sample stops as our network
    if not os.path.exists(SAMPLE_STOPS):
        raise SystemExit("Missing gtfs_sample/stops.csv")

    stops = []
    with open(SAMPLE_STOPS, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            stops.append(r)

    # 1) stops.txt
    write_csv(os.path.join(GTFS_DIR, "stops.txt"),
        ["stop_id","stop_name","stop_lat","stop_lon","location_type","parent_station"],
        [{
            "stop_id": s["stop_id"],
            "stop_name": s["stop_name"],
            "stop_lat": s["stop_lat"],
            "stop_lon": s["stop_lon"],
            "location_type": "0",
            "parent_station": ""
        } for s in stops]
    )

    # 2) routes.txt
    routes = [{"route_id": "R1", "route_short_name": "R1", "route_long_name": "Main Corridor", "route_type": "3"}]
    write_csv(os.path.join(GTFS_DIR, "routes.txt"),
        ["route_id","route_short_name","route_long_name","route_type"], routes)

    # 3) calendar.txt (service active daily for a wide range)
    today = datetime.utcnow().date()
    start = (today - timedelta(days=7)).strftime("%Y%m%d")
    end   = (today + timedelta(days=60)).strftime("%Y%m%d")
    cal = [{
        "service_id":"WEEK",
        "monday":"1","tuesday":"1","wednesday":"1","thursday":"1","friday":"1","saturday":"1","sunday":"1",
        "start_date":start,"end_date":end
    }]
    write_csv(os.path.join(GTFS_DIR, "calendar.txt"),
        ["service_id","monday","tuesday","wednesday","thursday","friday","saturday","sunday","start_date","end_date"], cal)

    # 4) trips.txt (one trip template repeated many times with headway)
    trips = []
    stop_times = []

    headway_min = 10
    service_hours = 18
    n_trips = (service_hours * 60) // headway_min

    base_dt = datetime.combine(today, datetime.min.time()).replace(hour=6, minute=0, second=0)

    for k in range(n_trips):
        dep0 = base_dt + timedelta(minutes=headway_min * k)
        trip_id = f"T{k:04d}"
        trips.append({"route_id":"R1","service_id":"WEEK","trip_id":trip_id})

        t = dep0
        # make the bus go through all stops sequentially, 2 minutes apart
        for i, s in enumerate(stops):
            arr = t + timedelta(minutes=2*i)
            dep = arr + timedelta(seconds=30)
            stop_times.append({
                "trip_id": trip_id,
                "arrival_time": hhmmss(arr),
                "departure_time": hhmmss(dep),
                "stop_id": s["stop_id"],
                "stop_sequence": str(i+1)
            })

    write_csv(os.path.join(GTFS_DIR, "trips.txt"),
        ["route_id","service_id","trip_id"], trips)

    write_csv(os.path.join(GTFS_DIR, "stop_times.txt"),
        ["trip_id","arrival_time","departure_time","stop_id","stop_sequence"], stop_times)

    # calendar_dates.txt optional (empty)
    write_csv(os.path.join(GTFS_DIR, "calendar_dates.txt"),
        ["service_id","date","exception_type"], [])

    print(f"Generated GTFS in {GTFS_DIR}")
    print(f"Stops: {len(stops)} | Trips: {len(trips)} | StopTimes: {len(stop_times)}")
    print("Restart your backend to reload GTFS.")
    
if __name__ == "__main__":
    main()
