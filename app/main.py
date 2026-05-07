from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import List
import asyncio
import json
import random
import pickle
import os
import sqlite3
from datetime import datetime
import pandas as pd

app = FastAPI(title="Smart Traffic API", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Database Setup ─────────────────────────────────────────────
DB_PATH = "traffic.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # Incidents table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            location TEXT NOT NULL,
            reported_at TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            resolved_at TEXT
        )
    ''')

    # Traffic logs table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS traffic_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            junction_id INTEGER NOT NULL,
            junction_name TEXT NOT NULL,
            vehicles INTEGER,
            wait_time INTEGER,
            status TEXT,
            logged_at TEXT NOT NULL
        )
    ''')

    # Emergency logs table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS emergency_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_type TEXT NOT NULL,
            action TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    ''')

    # AI predictions log table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ai_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            junction_id INTEGER,
            congestion_label TEXT,
            confidence REAL,
            predicted_wait INTEGER,
            recommended_green INTEGER,
            predicted_at TEXT NOT NULL
        )
    ''')

    conn.commit()
    conn.close()
    print("Database initialized successfully!")

# Initialize DB on startup
init_db()

# ── Load AI Models ─────────────────────────────────────────────
def load_models():
    models = {}
    try:
        with open('models/congestion_model.pkl', 'rb') as f:
            models['congestion'] = pickle.load(f)
        with open('models/waittime_model.pkl', 'rb') as f:
            models['waittime'] = pickle.load(f)
        with open('models/signal_model.pkl', 'rb') as f:
            models['signal'] = pickle.load(f)
        print("AI models loaded successfully!")
    except Exception as e:
        print(f"Could not load models: {e}")
    return models

ai_models = load_models()

# ── In-memory data ─────────────────────────────────────────────
junctions = [
    {"id": 1, "name": "Junction A - Main Street",  "lat": 11.0168, "lng": 76.9558, "status": "red",   "wait_time": 45, "vehicles": 32},
    {"id": 2, "name": "Junction B - Park Road",     "lat": 11.0200, "lng": 76.9600, "status": "green", "wait_time": 12, "vehicles": 18},
    {"id": 3, "name": "Junction C - Market Square", "lat": 11.0150, "lng": 76.9500, "status": "amber", "wait_time": 28, "vehicles": 45},
    {"id": 4, "name": "Junction D - College Road",  "lat": 11.0180, "lng": 76.9650, "status": "red",   "wait_time": 38, "vehicles": 27},
]

emergency_active = {"active": False, "vehicle_type": None, "activated_at": None}
connected_clients: List[WebSocket] = []

# ── DB Helper Functions ────────────────────────────────────────
def db_save_incident(type, location):
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute(
        "INSERT INTO incidents (type, location, reported_at, status) VALUES (?, ?, ?, 'active')",
        (type, location, now)
    )
    conn.commit()
    incident_id = cursor.lastrowid
    conn.close()
    return incident_id

def db_get_incidents():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM incidents ORDER BY reported_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def db_resolve_incident(incident_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE incidents SET status='resolved', resolved_at=? WHERE id=?",
        (datetime.now().isoformat(), incident_id)
    )
    conn.commit()
    conn.close()

def db_log_traffic(junction):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO traffic_logs (junction_id, junction_name, vehicles, wait_time, status, logged_at) VALUES (?, ?, ?, ?, ?, ?)",
        (junction['id'], junction['name'], junction['vehicles'], junction['wait_time'], junction['status'], datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def db_log_emergency(vehicle_type, action):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO emergency_logs (vehicle_type, action, timestamp) VALUES (?, ?, ?)",
        (vehicle_type, action, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def db_log_ai_prediction(junction_id, prediction):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO ai_predictions (junction_id, congestion_label, confidence, predicted_wait, recommended_green, predicted_at) VALUES (?, ?, ?, ?, ?, ?)",
        (junction_id, prediction['congestion_label'], prediction['confidence'],
         prediction['predicted_wait_seconds'], prediction['recommended_green_seconds'],
         datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def db_get_traffic_history(junction_id=None, limit=50):
    conn = get_db()
    cursor = conn.cursor()
    if junction_id:
        cursor.execute(
            "SELECT * FROM traffic_logs WHERE junction_id=? ORDER BY logged_at DESC LIMIT ?",
            (junction_id, limit)
        )
    else:
        cursor.execute(
            "SELECT * FROM traffic_logs ORDER BY logged_at DESC LIMIT ?",
            (limit,)
        )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def db_get_stats():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as total FROM incidents")
    total_incidents = cursor.fetchone()['total']
    cursor.execute("SELECT COUNT(*) as total FROM incidents WHERE status='active'")
    active_incidents = cursor.fetchone()['total']
    cursor.execute("SELECT COUNT(*) as total FROM emergency_logs WHERE action='activated'")
    total_emergencies = cursor.fetchone()['total']
    cursor.execute("SELECT COUNT(*) as total FROM traffic_logs")
    total_logs = cursor.fetchone()['total']
    cursor.execute("SELECT COUNT(*) as total FROM ai_predictions")
    total_predictions = cursor.fetchone()['total']
    conn.close()
    return {
        "total_incidents": total_incidents,
        "active_incidents": active_incidents,
        "total_emergencies": total_emergencies,
        "total_traffic_logs": total_logs,
        "total_ai_predictions": total_predictions,
    }

# ── AI Predict ─────────────────────────────────────────────────
def ai_predict(junction_id, vehicles, weather=0, incident_nearby=0):
    if not ai_models:
        return None
    try:
        now = datetime.now()
        hour = now.hour
        day_of_week = now.weekday()
        is_weekend = 1 if day_of_week >= 5 else 0
        is_peak_morning = 1 if 7 <= hour <= 9 else 0
        is_peak_evening = 1 if 16 <= hour <= 19 else 0

        base_input = {
            'hour': hour, 'day_of_week': day_of_week, 'junction_id': junction_id,
            'is_weekend': is_weekend, 'is_peak_morning': is_peak_morning,
            'is_peak_evening': is_peak_evening, 'weather': weather,
            'incident_nearby': incident_nearby
        }

        cm = ai_models['congestion']
        congestion_df = pd.DataFrame([{f: base_input[f] for f in cm['features']}])
        congestion_level = int(cm['model'].predict(congestion_df)[0])
        confidence = float(max(cm['model'].predict_proba(congestion_df)[0]))

        wm = ai_models['waittime']
        waittime_input = {**base_input, 'vehicles': vehicles}
        waittime_df = pd.DataFrame([{f: waittime_input[f] for f in wm['features']}])
        predicted_wait = float(wm['model'].predict(waittime_df)[0])

        sm = ai_models['signal']
        signal_df = pd.DataFrame([{f: base_input[f] for f in sm['features']}])
        recommended_green = float(sm['model'].predict(signal_df)[0])

        labels = {0: 'Low', 1: 'Medium', 2: 'High'}
        colors = {0: 'green', 1: 'amber', 2: 'red'}

        return {
            'congestion_level': congestion_level,
            'congestion_label': labels[congestion_level],
            'congestion_color': colors[congestion_level],
            'confidence': round(confidence * 100, 1),
            'predicted_wait_seconds': round(predicted_wait),
            'recommended_green_seconds': round(recommended_green),
            'is_peak_hour': bool(is_peak_morning or is_peak_evening),
        }
    except Exception as e:
        print(f"AI prediction error: {e}")
        return None

# ── REST Endpoints ─────────────────────────────────────────────
@app.get("/")
def root():
    return {"message": "Smart Traffic API v3.0 with AI + Database!", "ai_loaded": bool(ai_models), "version": "3.0.0"}

@app.get("/junctions")
def get_junctions():
    return {"junctions": junctions, "total": len(junctions)}

@app.get("/junctions/{junction_id}")
def get_junction(junction_id: int):
    for j in junctions:
        if j["id"] == junction_id:
            return j
    return {"error": "Junction not found"}

@app.get("/traffic/summary")
def get_traffic_summary():
    total_vehicles = sum(j["vehicles"] for j in junctions)
    avg_wait = sum(j["wait_time"] for j in junctions) // len(junctions)
    db_stats = db_get_stats()
    return {
        "total_vehicles": total_vehicles,
        "avg_wait_time": avg_wait,
        "active_signals": len(junctions),
        "red_signals": sum(1 for j in junctions if j["status"] == "red"),
        "green_signals": sum(1 for j in junctions if j["status"] == "green"),
        "incidents": db_stats["active_incidents"],
        "total_incidents_ever": db_stats["total_incidents"],
        "total_emergencies": db_stats["total_emergencies"],
        "total_ai_predictions": db_stats["total_ai_predictions"],
        "emergency_active": emergency_active["active"],
        "timestamp": datetime.now().isoformat(),
    }

# ── AI Endpoints ───────────────────────────────────────────────
@app.get("/ai/predict/{junction_id}")
def predict_junction(junction_id: int, weather: int = 0, incident: int = 0):
    junction = next((j for j in junctions if j["id"] == junction_id), None)
    if not junction:
        return {"error": "Junction not found"}
    prediction = ai_predict(junction_id, junction["vehicles"], weather, incident)
    if prediction:
        junction["status"] = prediction["congestion_color"]
        junction["wait_time"] = prediction["predicted_wait_seconds"]
        db_log_ai_prediction(junction_id, prediction)
        return {"junction_id": junction_id, "junction_name": junction["name"], **prediction}
    return {"error": "AI model not available"}

@app.get("/ai/predict/all")
def predict_all_junctions():
    results = []
    for j in junctions:
        prediction = ai_predict(j["id"], j["vehicles"])
        if prediction:
            j["status"] = prediction["congestion_color"]
            j["wait_time"] = prediction["predicted_wait_seconds"]
            db_log_ai_prediction(j["id"], prediction)
            results.append({"junction_id": j["id"], "junction_name": j["name"], **prediction})
    return {"predictions": results, "timestamp": datetime.now().isoformat(), "model_version": "XGBoost v1.0"}

@app.get("/ai/forecast")
def forecast_traffic():
    now = datetime.now()
    forecast = []
    for hour_offset in range(6):
        future_hour = (now.hour + hour_offset) % 24
        is_peak_morning = 1 if 7 <= future_hour <= 9 else 0
        is_peak_evening = 1 if 16 <= future_hour <= 19 else 0
        if is_peak_morning or is_peak_evening:
            level, color = "High", "red"
            vehicles = random.randint(700, 1000)
        elif 10 <= future_hour <= 15:
            level, color = "Medium", "amber"
            vehicles = random.randint(400, 700)
        else:
            level, color = "Low", "green"
            vehicles = random.randint(100, 400)
        forecast.append({
            "hour": f"{future_hour:02d}:00",
            "congestion_label": level,
            "congestion_color": color,
            "predicted_vehicles": vehicles,
            "is_peak": bool(is_peak_morning or is_peak_evening),
        })
    return {"forecast": forecast, "generated_at": datetime.now().isoformat()}

@app.get("/ai/status")
def ai_status():
    return {
        "ai_enabled": bool(ai_models),
        "models_loaded": list(ai_models.keys()) if ai_models else [],
        "model_version": "XGBoost 3.2.0",
        "accuracy": "94.4%",
        "last_trained": "2026-03-19",
    }

# ── Emergency Endpoints ────────────────────────────────────────
@app.post("/emergency/activate")
def activate_emergency(data: dict):
    vehicle_type = data.get("vehicle_type", "Ambulance")
    emergency_active["active"] = True
    emergency_active["vehicle_type"] = vehicle_type
    emergency_active["activated_at"] = datetime.now().isoformat()
    for j in junctions:
        j["status"] = "green"
        j["wait_time"] = 0
    db_log_emergency(vehicle_type, "activated")
    return {"message": f"{vehicle_type} emergency corridor activated!", "all_signals": "GREEN", "junctions_cleared": len(junctions)}

@app.post("/emergency/deactivate")
def deactivate_emergency():
    vehicle_type = emergency_active.get("vehicle_type", "Unknown")
    emergency_active["active"] = False
    emergency_active["vehicle_type"] = None
    statuses = ["red", "green", "amber"]
    for j in junctions:
        j["status"] = random.choice(statuses)
        j["wait_time"] = random.randint(10, 60)
    db_log_emergency(vehicle_type, "deactivated")
    return {"message": "Emergency deactivated. Signals returned to normal."}

@app.get("/emergency/status")
def get_emergency_status():
    return emergency_active

@app.get("/emergency/history")
def get_emergency_history():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM emergency_logs ORDER BY timestamp DESC LIMIT 20")
    rows = cursor.fetchall()
    conn.close()
    return {"history": [dict(row) for row in rows]}

# ── Incident Endpoints ─────────────────────────────────────────
@app.post("/incidents/report")
def report_incident(data: dict):
    type_ = data.get("type", "Unknown")
    location = data.get("location", "Unknown")
    incident_id = db_save_incident(type_, location)
    incident = {
        "id": incident_id,
        "type": type_,
        "location": location,
        "reported_at": datetime.now().isoformat(),
        "status": "active",
    }
    for j in junctions:
        if j["name"] == location:
            j["vehicles"] = j["vehicles"] + random.randint(5, 15)
            j["wait_time"] = j["wait_time"] + random.randint(10, 20)
    return {"message": "Incident reported and saved to database!", "incident": incident}

@app.get("/incidents")
def get_incidents():
    incidents = db_get_incidents()
    return {"incidents": incidents, "total": len(incidents)}

@app.post("/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: int):
    db_resolve_incident(incident_id)
    return {"message": f"Incident {incident_id} resolved successfully!"}

# ── Traffic History ────────────────────────────────────────────
@app.get("/history/traffic")
def get_traffic_history(junction_id: int = None, limit: int = 50):
    history = db_get_traffic_history(junction_id, limit)
    return {"history": history, "total": len(history)}

@app.get("/history/stats")
def get_history_stats():
    return db_get_stats()

# ── Route & Analytics ──────────────────────────────────────────
@app.get("/route/suggest")
def suggest_route():
    return {
        "current_route": {"name": "Main Street -> Park Road", "time_minutes": 18, "distance_km": 4.2, "congestion": "high"},
        "suggested_route": {"name": "College Road -> Market Square", "time_minutes": 11, "distance_km": 3.8, "congestion": "low"},
        "time_saved_minutes": 7,
    }

@app.get("/analytics/summary")
def get_analytics():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT strftime('%H', logged_at) as hour, AVG(vehicles) as avg_vehicles
        FROM traffic_logs
        GROUP BY hour
        ORDER BY hour
        LIMIT 8
    """)
    rows = cursor.fetchall()
    conn.close()

    if rows:
        hourly_data = [{"hour": f"{row['hour']}:00", "value": round(row['avg_vehicles'] / 100, 2)} for row in rows]
    else:
        hourly_data = [
            {"hour": "8am", "value": 0.4}, {"hour": "9am", "value": 0.7},
            {"hour": "10am", "value": 0.5}, {"hour": "12pm", "value": 0.6},
            {"hour": "2pm", "value": 0.45}, {"hour": "4pm", "value": 0.8},
            {"hour": "5pm", "value": 1.0}, {"hour": "6pm", "value": 0.75},
        ]

    return {
        "peak_hour": "5:00 PM",
        "busiest_junction": "Junction A - Main Street",
        "avg_wait_time_seconds": 38,
        "emergency_response_time_minutes": 4.2,
        "hourly_data": hourly_data,
        "congestion_by_junction": [
            {"junction": "Junction A", "percent": 85},
            {"junction": "Junction B", "percent": 52},
            {"junction": "Junction C", "percent": 38},
            {"junction": "Junction D", "percent": 65},
        ],
    }

# ── WebSocket ──────────────────────────────────────────────────
log_counter = 0

@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    global log_counter
    await websocket.accept()
    connected_clients.append(websocket)
    try:
        while True:
            for j in junctions:
                if not emergency_active["active"]:
                    j["vehicles"] = max(0, j["vehicles"] + random.randint(-3, 5))
                    j["wait_time"] = max(5, j["wait_time"] + random.randint(-5, 5))

            # Save to DB every 10 updates
            log_counter += 1
            if log_counter % 10 == 0:
                for j in junctions:
                    db_log_traffic(j)

            await websocket.send_text(json.dumps({
                "type": "junction_update",
                "junctions": junctions,
                "timestamp": datetime.now().isoformat(),
            }))
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        connected_clients.remove(websocket)

