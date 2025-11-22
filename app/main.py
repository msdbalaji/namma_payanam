# backend/app/main.py
import csv
import os
import math
from datetime import datetime, date, time, timedelta
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Body
from fastapi.responses import JSONResponse
from typing import Dict, List, Optional, Tuple   # <-- added Tuple
import asyncio
import zipfile
import json
from fastapi.middleware.cors import CORSMiddleware
import requests

app = FastAPI(title="GTFS Transit Backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # dev; tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GTFS_PATH = os.path.join(os.path.dirname(__file__), '..', 'gtfs')
GTFS_ZIP  = os.path.join(os.path.dirname(__file__), '..', 'gtfs.zip')

# in-memory store
stops: Dict[str, Dict] = {}
stop_times_by_stop: Dict[str, List[Dict]] = {}
trips: Dict[str, Dict] = {}
routes: Dict[str, Dict] = {}
service_dates_by_service: Dict[str, List[date]] = {}
services_calendar: Dict[str, Dict] = {}

clients: List[WebSocket] = []

# ---------- utils ----------

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*(math.sin(dlambda/2)**2)
    return 2 * R * math.asin(math.sqrt(a))

def parse_time_to_seconds(timestr: str) -> int:
    h, m, s = [int(x) for x in timestr.split(':')]
    return h * 3600 + m * 60 + s

def seconds_to_timeobj(seconds: int) -> time:
    seconds = seconds % (24 * 3600)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return time(h, m, s)

def load_csv(path):
    with open(path, newline='', encoding='utf-8') as fh:
        return list(csv.DictReader(fh))

async def broadcast_json(obj):
    dead = []
    for ws in clients:
        try:
            await ws.send_text(json.dumps(obj))
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in clients:
            clients.remove(ws)

# ---------- GTFS load (with fallback sample stops) ----------

def load_gtfs():
    """
    Load GTFS if present; otherwise fall back to gtfs_sample/stops.csv
    so nearby stops & search work during development.
    """
    global stops, stop_times_by_stop, trips, routes, services_calendar, service_dates_by_service

    base_dir = os.path.dirname(__file__)
    gtfs_dir = os.path.abspath(os.path.join(base_dir, '..', 'gtfs'))
    gtfs_zip = os.path.abspath(os.path.join(base_dir, '..', 'gtfs.zip'))
    sample_csv = os.path.abspath(os.path.join(base_dir, '..', 'gtfs_sample', 'stops.csv'))

    def safe_load_csv(path):
        if os.path.exists(path):
            with open(path, newline='', encoding='utf-8') as fh:
                return list(csv.DictReader(fh))
        return []

    # Unzip if we have gtfs.zip but no folder
    if os.path.isfile(gtfs_zip) and not os.path.isdir(gtfs_dir):
        print("Found gtfs.zip — extracting into backend/gtfs")
        with zipfile.ZipFile(gtfs_zip, 'r') as z:
            z.extractall(gtfs_dir)

    print("Loading GTFS files...")
    stops_rows = safe_load_csv(os.path.join(gtfs_dir, 'stops.txt'))
    stop_times_rows = safe_load_csv(os.path.join(gtfs_dir, 'stop_times.txt'))
    trips_rows = safe_load_csv(os.path.join(gtfs_dir, 'trips.txt'))
    routes_rows = safe_load_csv(os.path.join(gtfs_dir, 'routes.txt'))
    calendar_rows = safe_load_csv(os.path.join(gtfs_dir, 'calendar.txt'))
    calendar_dates_rows = safe_load_csv(os.path.join(gtfs_dir, 'calendar_dates.txt'))

    # Fallback to sample CSV if no GTFS stops found
    if not stops_rows and os.path.exists(sample_csv):
        print("No GTFS stops found, falling back to gtfs_sample/stops.csv")
        with open(sample_csv, newline='', encoding='utf-8') as fh:
            stops_rows = list(csv.DictReader(fh))
        stop_times_rows = []
        trips_rows = []
        routes_rows = []
        calendar_rows = []
        calendar_dates_rows = []

    # Load stops
    stops = {}
    for r in stops_rows:
        lat_key = 'stop_lat' if 'stop_lat' in r else ('lat' if 'lat' in r else None)
        lon_key = 'stop_lon' if 'stop_lon' in r else ('lon' if 'lon' in r else None)
        if not lat_key or not lon_key:
            continue
        try:
            lat = float(r.get(lat_key) or 0)
            lon = float(r.get(lon_key) or 0)
        except Exception:
            continue

        sid = r.get('stop_id') or r.get('id') or f"STOP_{len(stops)+1}"
        name = r.get('stop_name') or r.get('name') or 'Stop'
        stops[sid] = {
            'stop_id': sid,
            'name': name,
            'lat': lat,
            'lon': lon,
            'raw': r
        }

    # Trips/routes (may be empty in dev)
    trips = {}
    for r in trips_rows:
        if 'trip_id' in r:
            trips[r['trip_id']] = r

    routes = {}
    for r in routes_rows:
        if 'route_id' in r:
            routes[r['route_id']] = r

    # Group stop_times by stop_id (may be empty in dev)
    stop_times_by_stop = {}
    for r in stop_times_rows:
        sid = r.get('stop_id')
        if not sid:
            continue
        stop_times_by_stop.setdefault(sid, []).append(r)

    # Calendar (optional)
    services_calendar = {}
    for r in calendar_rows:
        svc = r.get('service_id')
        if not svc:
            continue
        try:
            services_calendar[svc] = {
                'start_date': datetime.strptime(r['start_date'], '%Y%m%d').date(),
                'end_date': datetime.strptime(r['end_date'], '%Y%m%d').date(),
                'monday': int(r.get('monday', '0')),
                'tuesday': int(r.get('tuesday', '0')),
                'wednesday': int(r.get('wednesday', '0')),
                'thursday': int(r.get('thursday', '0')),
                'friday': int(r.get('friday', '0')),
                'saturday': int(r.get('saturday', '0')),
                'sunday': int(r.get('sunday', '0')),
            }
        except Exception:
            pass

    # Calendar exceptions (optional)
    service_dates_by_service = {}
    for r in calendar_dates_rows:
        svc = r.get('service_id')
        dt = r.get('date')
        if not svc or not dt:
            continue
        try:
            d = datetime.strptime(dt, '%Y%m%d').date()
            service_dates_by_service.setdefault(svc, []).append({
                'date': d, 'exception_type': r.get('exception_type')
            })
        except Exception:
            pass

    print(f"Loaded {len(stops)} stops, {len(trips)} trips, {len(stop_times_by_stop)} stop_times groups")
load_gtfs()

# ---------- calendars ----------

def service_active_on(service_id: str, the_date: date) -> bool:
    # exceptions
    if service_id in service_dates_by_service:
        for ex in service_dates_by_service[service_id]:
            if ex['date'] == the_date:
                return ex['exception_type'] == '1'
    cal = services_calendar.get(service_id)
    if not cal: return False
    if not (cal['start_date'] <= the_date <= cal['end_date']): return False
    weekday = the_date.weekday()
    wk = [cal['monday'], cal['tuesday'], cal['wednesday'], cal['thursday'], cal['friday'], cal['saturday'], cal['sunday']]
    return wk[weekday] == 1

# ---------- departures (GTFS or synthetic) ----------

def get_upcoming_for_stop(stop_id: str, now_dt: datetime, window_minutes: int = 120) -> List[str]:
    entries = stop_times_by_stop.get(stop_id, [])
    # if we basically have no stop_times at all, synthesize headways
    if not entries and all(len(v) == 0 for v in stop_times_by_stop.values()):
        out = []
        for k in range(12):  # 2 hours
            t = now_dt + timedelta(minutes=10 * k)
            out.append(f"{t.strftime('%H:%M')} (S1)")
        return out

    out = []
    today = now_dt.date()
    window_seconds = window_minutes * 60

    for st in entries:
        trip_id = st.get('trip_id')
        if not trip_id or trip_id not in trips: continue
        trip = trips[trip_id]
        service_id = trip.get('service_id')
        dep = st.get('departure_time') or st.get('arrival_time') or ''
        if not dep: continue
        try:
            secs = parse_time_to_seconds(dep)
        except: continue

        day_offset = secs // (24*3600)
        secs_mod   = secs % (24*3600)
        dep_date   = today + timedelta(days=day_offset)
        if not service_active_on(service_id, dep_date): continue
        dep_dt = datetime(dep_date.year, dep_date.month, dep_date.day) + timedelta(seconds=secs_mod)
        delta = (dep_dt - now_dt).total_seconds()
        if -60 <= delta <= window_seconds:
            route_id   = trip.get('route_id')
            route_name = routes.get(route_id, {}).get('route_short_name') or routes.get(route_id, {}).get('route_long_name') or route_id or ''
            out.append(f"{dep_dt.strftime('%H:%M')} ({route_name})")

    def time_key(s):
        try:
            hh, mm = s.split()[0].split(':')
            return int(hh)*3600 + int(mm)*60
        except: return 0

    return sorted(list(dict.fromkeys(out)), key=time_key)

# ---------- endpoints ----------

@app.get("/stops/nearby")
async def stops_nearby(lat: float, lon: float, radius: int = 700000, max_results: int = 100):
    center_lat, center_lon = float(lat), float(lon)
    result = []
    for s in stops.values():
        d = int(haversine(center_lat, center_lon, s['lat'], s['lon']))
        if d <= radius:
            result.append((d, s))
    result.sort(key=lambda x: x[0])
    now = datetime.utcnow()
    out = []
    for d, s in result[:max_results]:
        upcoming = get_upcoming_for_stop(s['stop_id'], now, window_minutes=120)
        out.append({
            'stop_id': s['stop_id'],
            'name': s['name'],
            'lat': s['lat'],
            'lon': s['lon'],
            'distance_m': d,
            'upcoming': ', '.join(upcoming[:3]) if upcoming else '—',
            'departures': upcoming
        })
    return JSONResponse(out)

@app.get("/places/search")
async def places_search(q: str):
    # lat,lon direct
    try:
        if ',' in q:
            a,b = q.split(',',1)
            return JSONResponse([{"name": q, "lat": float(a), "lon": float(b)}])
    except: pass

    results = []
    ql = q.strip().lower()

    # 1) stop-name substring (fast, offline)
    for s in stops.values():
        nm = (s['name'] or '').lower()
        if ql in nm:
            results.append({"name": s['name'], "lat": s['lat'], "lon": s['lon']})
            if len(results) >= 10: break

    # 2) Nominatim if fewer than 5 matches (common places)
    if len(results) < 5 and len(ql) >= 3:
        try:
            nomi = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": q, "format": "json", "limit": 8, "addressdetails": 0},
                headers={"User-Agent": "madurai-transit/1.0"}
            , timeout=7)
            if nomi.status_code == 200:
                data = nomi.json()
                for itm in data:
                    try:
                        name = itm.get("display_name") or q
                        lat  = float(itm["lat"])
                        lon  = float(itm["lon"])
                        results.append({"name": name, "lat": lat, "lon": lon})
                    except: pass
        except Exception:
            pass

    return JSONResponse(results[:10])

@app.get("/routes/options")
async def routes_options(from_lat: float, from_lon: float, to_lat: float, to_lon: float):
    dist_m = haversine(from_lat, from_lon, to_lat, to_lon)
    km = dist_m / 1000.0
    walking_min = max(1, int(dist_m / 80))
    driving_min = max(1, int(dist_m / 400))
    auto_min    = max(1, int(dist_m / 300))
    auto_fare   = max(30, int(35 + 12 * km))
    private_cost = int(round(7.0 * km))

    return JSONResponse([
        {"mode":"walking", "duration_text":f"{walking_min} min", "distance_text":f"{km:.2f} km", "description":"Walk all the way"},
        {"mode":"private", "duration_text":f"{driving_min} min", "distance_text":f"{km:.2f} km", "description":f"Taxi/car • est. fuel ₹{private_cost}"},
        {"mode":"bus", "duration_text":f"{walking_min+12} min", "distance_text":f"{km:.2f} km", "description":"Walk to nearest stop + bus"},
        {"mode":"auto", "duration_text":f"{auto_min} min", "distance_text":f"{km:.2f} km", "description":f"Auto • est. fare ₹{auto_fare}"},
    ])

# ---------- WebSocket (sim) ----------

@app.websocket("/ws/buses")
async def ws_buses(websocket: WebSocket, simulate: Optional[bool] = Query(False)):
    await websocket.accept()
    clients.append(websocket)
    try:
        if simulate:
            base_lat, base_lon = 9.9300, 78.1181
            t = 0
            while True:
                payload = {"type":"bus","id":"sim-1",
                           "lat": base_lat + 0.006 * math.sin(t/25.0),
                           "lon": base_lon + 0.008 * math.cos(t/22.0),
                           "bearing": (t*9) % 360, "route":"S1"}
                await broadcast_json(payload)
                t += 1
                await asyncio.sleep(2)
        else:
            while True:
                try:
                    msg = await websocket.receive_text()
                    if msg:
                        await websocket.send_text(json.dumps({"status":"ok","echo":msg}))
                except WebSocketDisconnect:
                    break
    finally:
        if websocket in clients:
            clients.remove(websocket)

# optional live bus injector
@app.post("/buses/update")
async def update_bus(payload: Dict = Body(...)):
    payload.setdefault('type', 'bus')
    await broadcast_json(payload)
    return JSONResponse({"status":"ok","broadcasted":True})

@app.get("/health")
async def health():
    return JSONResponse({"status":"ok","time":datetime.utcnow().isoformat()})

# -----------------------------
# Simple bus-connection planner (kept this NEW version only)
# -----------------------------
def _nearest_stop(lat: float, lon: float) -> Optional[Dict]:
    best = None
    best_d = 1e12
    for s in stops.values():
        d = haversine(lat, lon, s['lat'], s['lon'])
        if d < best_d:
            best_d = d
            best = s
    if best:
        best = dict(best)
        best['distance_m'] = int(best_d)
    return best

def _walk_minutes(meters: float) -> int:
    return max(1, int(round(meters / 83.3)))   # ~5 km/h

def _bus_minutes(meters: float) -> int:
    return max(1, int(round(meters / 333.0)))  # ~20 km/h

def _auto_minutes(meters: float) -> int:
    return max(1, int(round(meters / 416.0)))  # ~25 km/h

def _private_minutes(meters: float) -> int:
    return max(1, int(round(meters / 583.0)))  # ~35 km/h

def _fare_estimate(mode: str, meters: float) -> int:
    km = max(1.0, meters / 1000.0)
    if mode == "bus":
        return int(round(6 + 2.5 * km))
    if mode == "auto":
        return int(round(30 + 15 * km))
    if mode == "private":
        return int(round(60 + 20 * km))
    return 0

HUB_IDS = ["MDR_MATTU","MDR_PERIYAR","MDR_ARAP","MDR_PALANG","MDR_GORIP"]

def _best_transfer(origin_stop: Dict, dest_stop: Dict) -> Optional[Dict]:
    best = None
    best_total = 1e12
    for hid in HUB_IDS:
        h = stops.get(hid)
        if not h:
            continue
        d1 = haversine(origin_stop['lat'], origin_stop['lon'], h['lat'], h['lon'])
        d2 = haversine(h['lat'], h['lon'], dest_stop['lat'], dest_stop['lon'])
        total = d1 + d2
        if total < best_total:
            best_total = total
            best = h
    return best

def _next_departure_iso(headway_min: int = 7) -> Tuple[str, int]:
    now = datetime.utcnow()
    wait_min = (headway_min - (now.minute % headway_min)) % headway_min
    if wait_min == 0:
        wait_min = headway_min
    dep = now + timedelta(minutes=wait_min)
    secs = int((dep - now).total_seconds())
    return dep.isoformat() + "Z", secs

@app.get("/plan/bus")
async def plan_bus(from_lat: float, from_lon: float, to_lat: float, to_lon: float):
    origin_ll = (float(from_lat), float(from_lon))
    dest_ll = (float(to_lat), float(to_lon))

    origin_stop = _nearest_stop(*origin_ll)
    dest_stop = _nearest_stop(*dest_ll)
    if not origin_stop or not dest_stop:
        return JSONResponse({"legs": [], "note": "No stops data"})

    walk1_m = haversine(origin_ll[0], origin_ll[1], origin_stop['lat'], origin_stop['lon'])
    walk2_m = haversine(dest_ll[0], dest_ll[1], dest_stop['lat'], dest_stop['lon'])

    d_bus = haversine(origin_stop['lat'], origin_stop['lon'], dest_stop['lat'], dest_stop['lon'])

    legs = []
    total_m = 0.0
    total_min = 0

    legs.append({
        "mode": "walk",
        "from": {"name": "Current location", "lat": origin_ll[0], "lon": origin_ll[1]},
        "to": {"name": origin_stop['name'], "lat": origin_stop['lat'], "lon": origin_stop['lon']},
        "distance_m": int(walk1_m),
        "duration_min": _walk_minutes(walk1_m),
    })
    total_m += walk1_m
    total_min += _walk_minutes(walk1_m)

    DIRECT_THRESHOLD_M = 7000
    if d_bus <= DIRECT_THRESHOLD_M:
        legs.append({
            "mode": "bus",
            "from_stop": {"id": origin_stop['stop_id'], "name": origin_stop['name'], "lat": origin_stop['lat'], "lon": origin_stop['lon']},
            "to_stop": {"id": dest_stop['stop_id'], "name": dest_stop['name'], "lat": dest_stop['lat'], "lon": dest_stop['lon']},
            "distance_m": int(d_bus),
            "duration_min": _bus_minutes(d_bus),
            "route_name": "City Bus",
        })
        total_m += d_bus
        total_min += _bus_minutes(d_bus)
    else:
        transfer = _best_transfer(origin_stop, dest_stop) or dest_stop
        d1 = haversine(origin_stop['lat'], origin_stop['lon'], transfer['lat'], transfer['lon'])
        d2 = haversine(transfer['lat'], transfer['lon'], dest_stop['lat'], dest_stop['lon'])

        legs.append({
            "mode": "bus",
            "from_stop": {"id": origin_stop['stop_id'], "name": origin_stop['name'], "lat": origin_stop['lat'], "lon": origin_stop['lon']},
            "to_stop": {"id": transfer['stop_id'], "name": transfer['name'], "lat": transfer['lat'], "lon": transfer['lon']},
            "distance_m": int(d1),
            "duration_min": _bus_minutes(d1),
            "route_name": "Feeder",
        })
        legs.append({
            "mode": "bus",
            "from_stop": {"id": transfer['stop_id'], "name": transfer['name'], "lat": transfer['lat'], "lon": transfer['lon']},
            "to_stop": {"id": dest_stop['stop_id'], "name": dest_stop['name'], "lat": dest_stop['lat'], "lon": dest_stop['lon']},
            "distance_m": int(d2),
            "duration_min": _bus_minutes(d2),
            "route_name": "Connector",
        })
        total_m += d1 + d2
        total_min += _bus_minutes(d1) + _bus_minutes(d2)

    legs.append({
        "mode": "walk",
        "from": {"name": dest_stop['name'], "lat": dest_stop['lat'], "lon": dest_stop['lon']},
        "to": {"name": "Destination", "lat": dest_ll[0], "lon": dest_ll[1]},
        "distance_m": int(walk2_m),
        "duration_min": _walk_minutes(walk2_m),
    })
    total_m += walk2_m
    total_min += _walk_minutes(walk2_m)

    dep_iso, countdown_sec = _next_departure_iso(headway_min=7)

    bus_distance = sum(l.get("distance_m", 0) for l in legs if l["mode"] == "bus")
    fare_bus = _fare_estimate("bus", bus_distance)
    fare_auto = _fare_estimate("auto", total_m)
    fare_private = _fare_estimate("private", total_m)

    return JSONResponse({
        "legs": legs,
        "summary": {
            "total_distance_m": int(total_m),
            "total_duration_min": int(total_min + max(0, countdown_sec // 60)),
            "fare_bus_rs": fare_bus,
            "fare_auto_rs": fare_auto,
            "fare_private_rs": fare_private,
            "next_departure_iso": dep_iso,
            "countdown_sec": countdown_sec,
        }
    })
