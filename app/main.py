from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
import asyncio
import json
import random
import pickle
import os
import sqlite3
import math
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
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS emergency_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_type TEXT NOT NULL,
            action TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    ''')
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
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS active_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            lat REAL NOT NULL,
            lng REAL NOT NULL,
            last_seen TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()
    print("Database initialized successfully!")

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

# ── Junction Data — 60 Real Tamil Nadu Junctions ───────────────
junctions = [
   
   # ==========================================
# 1. CHENNAI REGION (Capital & Transit Hubs)
# ==========================================
{"id": 1, "name": "Anna Salai, Chennai", "lat": 13.0600, "lng": 80.2500, "status": "red", "wait_time": 55, "vehicles": 0},
{"id": 2, "name": "T Nagar, Chennai", "lat": 13.0418, "lng": 80.2341, "status": "red", "wait_time": 60, "vehicles": 0},
{"id": 3, "name": "Koyambedu, Chennai", "lat": 13.0694, "lng": 80.1948, "status": "amber", "wait_time": 40, "vehicles": 0},
{"id": 4, "name": "Vadapalani, Chennai", "lat": 13.0530, "lng": 80.2120, "status": "green", "wait_time": 18, "vehicles": 0},
{"id": 5, "name": "Adyar, Chennai", "lat": 13.0012, "lng": 80.2565, "status": "red", "wait_time": 50, "vehicles": 0},
{"id": 6, "name": "Tambaram, Chennai", "lat": 12.9249, "lng": 80.1000, "status": "amber", "wait_time": 35, "vehicles": 0},
{"id": 7, "name": "Guindy, Chennai", "lat": 13.0067, "lng": 80.2206, "status": "red", "wait_time": 45, "vehicles": 0},
{"id": 8, "name": "Velachery, Chennai", "lat": 12.9815, "lng": 80.2180, "status": "green", "wait_time": 20, "vehicles": 0},
{"id": 9, "name": "Porur, Chennai", "lat": 13.0363, "lng": 80.1572, "status": "amber", "wait_time": 30, "vehicles": 0},
{"id": 10, "name": "Chromepet, Chennai", "lat": 12.9516, "lng": 80.1413, "status": "red", "wait_time": 42, "vehicles": 0},
{"id": 11, "name": "Sholinganallur, Chennai", "lat": 12.9010, "lng": 80.2279, "status": "green", "wait_time": 15, "vehicles": 0},
{"id": 12, "name": "Perambur, Chennai", "lat": 13.1167, "lng": 80.2333, "status": "amber", "wait_time": 28, "vehicles": 0},
{"id": 13, "name": "Ambattur, Chennai", "lat": 13.0982, "lng": 80.1614, "status": "red", "wait_time": 40, "vehicles": 0},
{"id": 14, "name": "Avadi, Chennai", "lat": 13.1147, "lng": 80.1017, "status": "green", "wait_time": 15, "vehicles": 0},
{"id": 15, "name": "Poonamallee, Chennai", "lat": 13.0473, "lng": 80.0945, "status": "red", "wait_time": 48, "vehicles": 0},
{"id": 16, "name": "Central Railway Station, Chennai", "lat": 13.0827, "lng": 80.2707, "status": "red", "wait_time": 52, "vehicles": 0},

# ==========================================
# 2. COIMBATORE REGION (Western TN)
# ==========================================
{"id": 17, "name": "Gandhipuram, Coimbatore", "lat": 11.0168, "lng": 76.9558, "status": "red", "wait_time": 45, "vehicles": 0},
{"id": 18, "name": "RS Puram, Coimbatore", "lat": 11.0050, "lng": 76.9620, "status": "green", "wait_time": 12, "vehicles": 0},
{"id": 19, "name": "Ukkadam, Coimbatore", "lat": 10.9925, "lng": 76.9612, "status": "amber", "wait_time": 28, "vehicles": 0},
{"id": 20, "name": "Peelamedu, Coimbatore", "lat": 11.0280, "lng": 77.0090, "status": "red", "wait_time": 38, "vehicles": 0},
{"id": 21, "name": "Singanallur, Coimbatore", "lat": 11.0020, "lng": 77.0180, "status": "green", "wait_time": 15, "vehicles": 0},
{"id": 22, "name": "Hopes College, Coimbatore", "lat": 11.0230, "lng": 76.9480, "status": "amber", "wait_time": 22, "vehicles": 0},
{"id": 23, "name": "Saibaba Colony, Coimbatore", "lat": 11.0200, "lng": 76.9700, "status": "green", "wait_time": 10, "vehicles": 0},
{"id": 24, "name": "Tidel Park, Coimbatore", "lat": 11.0120, "lng": 77.0050, "status": "red", "wait_time": 35, "vehicles": 0},
{"id": 25, "name": "Thudiyalur, Coimbatore", "lat": 11.0772, "lng": 76.9360, "status": "amber", "wait_time": 24, "vehicles": 0},
{"id": 26, "name": "Pollachi Bus Stand", "lat": 10.6620, "lng": 77.0065, "status": "red", "wait_time": 36, "vehicles": 0},

# ==========================================
# 3. MADURAI REGION (Southern Gateway)
# ==========================================
{"id": 27, "name": "Mattuthavani, Madurai", "lat": 9.9601, "lng": 78.1212, "status": "red", "wait_time": 48, "vehicles": 0},
{"id": 28, "name": "Anna Nagar, Madurai", "lat": 9.9252, "lng": 78.1198, "status": "green", "wait_time": 15, "vehicles": 0},
{"id": 29, "name": "Bypass Road, Madurai", "lat": 9.9750, "lng": 78.1500, "status": "amber", "wait_time": 25, "vehicles": 0},
{"id": 30, "name": "Meenakshi Temple, Madurai", "lat": 9.9195, "lng": 78.1193, "status": "red", "wait_time": 55, "vehicles": 0},
{"id": 31, "name": "Goripalayam, Madurai", "lat": 9.9310, "lng": 78.1280, "status": "amber", "wait_time": 32, "vehicles": 0},
{"id": 32, "name": "Periyar Bus Stand, Madurai", "lat": 9.9172, "lng": 78.1130, "status": "red", "wait_time": 42, "vehicles": 0},
{"id": 33, "name": "Thirumangalam, Madurai", "lat": 9.8236, "lng": 77.9975, "status": "green", "wait_time": 14, "vehicles": 0},

# ==========================================
# 4. TRICHY REGION (Central TN)
# ==========================================
{"id": 34, "name": "Srirangam, Trichy", "lat": 10.8650, "lng": 78.6930, "status": "amber", "wait_time": 30, "vehicles": 0},
{"id": 35, "name": "Chathiram Bus Stand, Trichy", "lat": 10.8050, "lng": 78.6856, "status": "red", "wait_time": 45, "vehicles": 0},
{"id": 36, "name": "Thillai Nagar, Trichy", "lat": 10.8231, "lng": 78.7011, "status": "green", "wait_time": 12, "vehicles": 0},
{"id": 37, "name": "Ariyamangalam, Trichy", "lat": 10.8400, "lng": 78.7400, "status": "red", "wait_time": 38, "vehicles": 0},
{"id": 38, "name": "Central Bus Stand, Trichy", "lat": 10.7933, "lng": 78.6822, "status": "red", "wait_time": 50, "vehicles": 0},
{"id": 39, "name": "BHEL Township, Trichy", "lat": 10.7914, "lng": 78.8028, "status": "green", "wait_time": 15, "vehicles": 0},

# ==========================================
# 5. SALEM REGION (North-Western TN)
# ==========================================
{"id": 40, "name": "Shevapet, Salem", "lat": 11.6590, "lng": 78.1580, "status": "red", "wait_time": 40, "vehicles": 0},
{"id": 41, "name": "Fairlands, Salem", "lat": 11.6744, "lng": 78.1460, "status": "green", "wait_time": 18, "vehicles": 0},
{"id": 42, "name": "Gugai, Salem", "lat": 11.6480, "lng": 78.1420, "status": "amber", "wait_time": 25, "vehicles": 0},
{"id": 43, "name": "Omalur Road, Salem", "lat": 11.6650, "lng": 78.1700, "status": "red", "wait_time": 35, "vehicles": 0},
{"id": 44, "name": "New Bus Stand, Salem", "lat": 11.6684, "lng": 78.1189, "status": "red", "wait_time": 44, "vehicles": 0},
{"id": 45, "name": "Attur Bus Stand", "lat": 11.5975, "lng": 78.5976, "status": "amber", "wait_time": 20, "vehicles": 0},

# ==========================================
# 6. ERODE & TIRUPPUR REGION (Industrial Belt)
# ==========================================
{"id": 46, "name": "Erode Bus Stand", "lat": 11.3410, "lng": 77.7172, "status": "red", "wait_time": 42, "vehicles": 0},
{"id": 47, "name": "Perundurai, Erode", "lat": 11.2760, "lng": 77.5880, "status": "green", "wait_time": 15, "vehicles": 0},
{"id": 48, "name": "Bhavani, Erode", "lat": 11.4470, "lng": 77.6830, "status": "amber", "wait_time": 22, "vehicles": 0},
{"id": 49, "name": "Tiruppur Bus Stand", "lat": 11.1085, "lng": 77.3411, "status": "red", "wait_time": 45, "vehicles": 0},
{"id": 50, "name": "Avinashi Road, Tiruppur", "lat": 11.1200, "lng": 77.3600, "status": "amber", "wait_time": 30, "vehicles": 0},
{"id": 51, "name": "Palladam Checkpost, Tiruppur", "lat": 10.9880, "lng": 77.2794, "status": "red", "wait_time": 32, "vehicles": 0},

# ==========================================
# 7. VELLORE & NORTHERN DISTRICTS
# ==========================================
{"id": 52, "name": "Vellore Bus Stand", "lat": 12.9165, "lng": 79.1325, "status": "red", "wait_time": 40, "vehicles": 0},
{"id": 53, "name": "Katpadi, Vellore", "lat": 12.9700, "lng": 79.1500, "status": "green", "wait_time": 18, "vehicles": 0},
{"id": 54, "name": "CMC Hospital, Vellore", "lat": 12.9240, "lng": 79.1350, "status": "amber", "wait_time": 25, "vehicles": 0},
{"id": 55, "name": "Ranipet Muthukadai", "lat": 12.9276, "lng": 79.3323, "status": "amber", "wait_time": 20, "vehicles": 0},
{"id": 56, "name": "Ambur Bus Stand", "lat": 12.7845, "lng": 78.7114, "status": "green", "wait_time": 15, "vehicles": 0},
{"id": 57, "name": "Vaniyambadi Junction", "lat": 12.6840, "lng": 78.6190, "status": "amber", "wait_time": 18, "vehicles": 0},
{"id": 58, "name": "Tirupattur Bus Stand", "lat": 12.4921, "lng": 78.5678, "status": "green", "wait_time": 12, "vehicles": 0},

# ==========================================
# 8. DELTA REGION (Thanjavur, Tiruvarur, Nagai)
# ==========================================
{"id": 59, "name": "Thanjavur Bus Stand", "lat": 10.7870, "lng": 79.1378, "status": "amber", "wait_time": 28, "vehicles": 0},
{"id": 60, "name": "Medical College, Thanjavur", "lat": 10.8000, "lng": 79.1500, "status": "green", "wait_time": 15, "vehicles": 0},
{"id": 61, "name": "Kumbakonam Uchchi Pillayar", "lat": 10.9617, "lng": 79.3881, "status": "red", "wait_time": 38, "vehicles": 0},
{"id": 62, "name": "Tiruvarur Bus Stand", "lat": 10.7739, "lng": 79.6339, "status": "green", "wait_time": 14, "vehicles": 0},
{"id": 63, "name": "Nagapattinam Beach Road", "lat": 10.7661, "lng": 79.8442, "status": "amber", "wait_time": 20, "vehicles": 0},
{"id": 64, "name": "Mayiladuthurai Junction", "lat": 11.1018, "lng": 79.6522, "status": "red", "wait_time": 30, "vehicles": 0},

# ==========================================
# 9. SOUTHERN DEEP SOUTH (Tirunelveli, Kanyakumari, Thoothukudi)
# ==========================================
{"id": 65, "name": "Palayamkottai, Tirunelveli", "lat": 8.7139, "lng": 77.7567, "status": "red", "wait_time": 38, "vehicles": 0},
{"id": 66, "name": "Junction, Tirunelveli", "lat": 8.7278, "lng": 77.6983, "status": "amber", "wait_time": 28, "vehicles": 0},
{"id": 67, "name": "Vannarpettai, Tirunelveli", "lat": 8.7350, "lng": 77.7100, "status": "green", "wait_time": 15, "vehicles": 0},
{"id": 68, "name": "Nagercoil Bus Stand", "lat": 8.1833, "lng": 77.4119, "status": "red", "wait_time": 35, "vehicles": 0},
{"id": 69, "name": "Marthandam, Nagercoil", "lat": 8.3082, "lng": 77.2293, "status": "amber", "wait_time": 22, "vehicles": 0},
{"id": 70, "name": "Thoothukudi VVD Signal", "lat": 8.8053, "lng": 78.1461, "status": "red", "wait_time": 34, "vehicles": 0},
{"id": 71, "name": "Ettayapuram Road, Thoothukudi", "lat": 8.8160, "lng": 78.1390, "status": "green", "wait_time": 16, "vehicles": 0},
{"id": 72, "name": "Kanyakumari Pier Road", "lat": 8.0793, "lng": 77.5540, "status": "amber", "wait_time": 25, "vehicles": 0},

# ==========================================
# 10. KRISHNAGIRI & HOSUR (Border Belt)
# ==========================================
{"id": 73, "name": "Hosur Bus Stand", "lat": 12.7409, "lng": 77.8253, "status": "amber", "wait_time": 30, "vehicles": 0},
{"id": 74, "name": "Sipcot, Hosur", "lat": 12.7500, "lng": 77.8400, "status": "green", "wait_time": 15, "vehicles": 0},
{"id": 75, "name": "Krishnagiri Roundana", "lat": 12.5255, "lng": 78.2146, "status": "red", "wait_time": 35, "vehicles": 0},

# ==========================================
# 11. CENTRAL HILLS & RECREATION (Ooty, Dindigul)
# ==========================================
{"id": 76, "name": "Dindigul Bus Stand", "lat": 10.3673, "lng": 77.9803, "status": "red", "wait_time": 38, "vehicles": 0},
{"id": 77, "name": "Palani Road, Dindigul", "lat": 10.3800, "lng": 77.9900, "status": "amber", "wait_time": 25, "vehicles": 0},
{"id": 78, "name": "Ooty Charring Cross", "lat": 11.4103, "lng": 76.7083, "status": "red", "wait_time": 40, "vehicles": 0},
{"id": 79, "name": "Coonoor Bus Stand", "lat": 11.3530, "lng": 76.7959, "status": "green", "wait_time": 12, "vehicles": 0},
{"id": 80, "name": "Kodaikanal Lake Road", "lat": 10.2319, "lng": 77.4922, "status": "amber", "wait_time": 20, "vehicles": 0},

# ==========================================
# 12. EAST COAST (Cuddalore, Puducherry)
# ==========================================
{"id": 81, "name": "Pondicherry Bus Stand", "lat": 11.9416, "lng": 79.8083, "status": "red", "wait_time": 45, "vehicles": 0},
{"id": 82, "name": "White Town, Pondicherry", "lat": 11.9340, "lng": 79.8330, "status": "green", "wait_time": 18, "vehicles": 0},
{"id": 83, "name": "Ariyankuppam, Pondicherry", "lat": 11.8960, "lng": 79.8200, "status": "amber", "wait_time": 25, "vehicles": 0},
{"id": 84, "name": "Cuddalore Bus Stand", "lat": 11.7447, "lng": 79.7689, "status": "red", "wait_time": 38, "vehicles": 0},
{"id": 85, "name": "Chidambaram Temple Car St", "lat": 11.3992, "lng": 79.6934, "status": "amber", "wait_time": 22, "vehicles": 0},

# ==========================================
# 13. KANCHIPURAM & TIRUVALLUR (Industrial/Heritage)
# ==========================================
{"id": 86, "name": "Kanchipuram Bus Stand", "lat": 12.8342, "lng": 79.7036, "status": "red", "wait_time": 40, "vehicles": 0},
{"id": 87, "name": "Silk Weaving Area, Kanchipuram", "lat": 12.8400, "lng": 79.7100, "status": "green", "wait_time": 18, "vehicles": 0},
{"id": 88, "name": "Tiruvallur Oil Mill Junction", "lat": 13.1444, "lng": 79.9084, "status": "amber", "wait_time": 26, "vehicles": 0},
{"id": 89, "name": "Sriperumbudur Rajiv Gandhi Circle", "lat": 12.9694, "lng": 79.9515, "status": "red", "wait_time": 35, "vehicles": 0},

# ==========================================
# 14. REMAINING DISTRICT CENTERS & HIGHWAYS
# ==========================================
{"id": 90, "name": "Villupuram Koliyanur Cross", "lat": 11.9401, "lng": 79.4950, "status": "red", "wait_time": 42, "vehicles": 0},
{"id": 91, "name": "Tindivanam NH Junction", "lat": 12.2424, "lng": 79.6499, "status": "amber", "wait_time": 28, "vehicles": 0},
{"id": 92, "name": "Dharmapuri Four Roads", "lat": 12.1311, "lng": 78.1590, "status": "red", "wait_time": 30, "vehicles": 0},
{"id": 93, "name": "Namakkal Park Road", "lat": 11.2189, "lng": 78.1672, "status": "green", "wait_time": 15, "vehicles": 0},
{"id": 94, "name": "Karur Bus Stand", "lat": 10.9602, "lng": 78.0766, "status": "amber", "wait_time": 25, "vehicles": 0},
{"id": 95, "name": "Pudukkottai Old Bus Stand", "lat": 10.3797, "lng": 78.8234, "status": "green", "wait_time": 16, "vehicles": 0},
{"id": 96, "name": "Karaikudi New Bus Stand", "lat": 10.0747, "lng": 78.7850, "status": "amber", "wait_time": 24, "vehicles": 0},
{"id": 97, "name": "Sivagangai Aranmanai Vasal", "lat": 9.8433, "lng": 78.4811, "status": "green", "wait_time": 12, "vehicles": 0},
{"id": 98, "name": "Ramanathapuram Aranmanai", "lat": 9.3716, "lng": 78.8310, "status": "amber", "wait_time": 20, "vehicles": 0},
{"id": 99, "name": "Rameswaram Temple Road", "lat": 9.2881, "lng": 79.3122, "status": "red", "wait_time": 35, "vehicles": 0},
{"id": 100, "name": "Virudhunagar MGR Bus Stand", "lat": 9.5872, "lng": 77.9575, "status": "green", "wait_time": 15, "vehicles": 0},
{"id": 101, "name": "Sivakasi Car Street", "lat": 9.4532, "lng": 77.8021, "status": "red", "wait_time": 33, "vehicles": 0},
{"id": 102, "name": "Rajapalayam Panthalgudi Rd", "lat": 9.4522, "lng": 77.5542, "status": "amber", "wait_time": 25, "vehicles": 0},
{"id": 103, "name": "Theni Old Bus Stand", "lat": 10.0104, "lng": 77.4764, "status": "red", "wait_time": 28, "vehicles": 0},
{"id": 104, "name": "Perambalur Four Roads", "lat": 11.2342, "lng": 78.8821, "status": "green", "wait_time": 14, "vehicles": 0},
{"id": 105, "name": "Ariyalur Bus Stand", "lat": 11.1378, "lng": 79.0792, "status": "amber", "wait_time": 18, "vehicles": 0}
]

# Initialize 4-way roads and alerts for each junction
for j in junctions:
    j["roads"] = {"north": "red", "south": "red", "east": "red", "west": "red"}
    j["emergency_alerts"] = []

emergency_active = {"active": False, "vehicle_type": None, "activated_at": None, "route_ids": []}
connected_clients: List[WebSocket] = []

# ── GPS Distance Calculator ────────────────────────────────────
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

# ── GPS User Functions ─────────────────────────────────────────
def update_user_location(user_id: str, role: str, lat: float, lng: float):
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute("SELECT id FROM active_users WHERE user_id=?", (user_id,))
    existing = cursor.fetchone()
    if existing:
        cursor.execute(
            "UPDATE active_users SET lat=?, lng=?, last_seen=?, role=? WHERE user_id=?",
            (lat, lng, now, role, user_id)
        )
    else:
        cursor.execute(
            "INSERT INTO active_users (user_id, role, lat, lng, last_seen) VALUES (?,?,?,?,?)",
            (user_id, role, lat, lng, now)
        )
    conn.commit()
    conn.close()

def count_users_near_junction(junction_lat, junction_lng, radius=1000):
    # radius increased to 1km for Tamil Nadu scale
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT lat, lng FROM active_users WHERE last_seen >= datetime('now', '-30 seconds')")
    users = cursor.fetchall()
    conn.close()
    count = 0
    for user in users:
        dist = haversine(junction_lat, junction_lng, user['lat'], user['lng'])
        if dist <= radius:
            count += 1
    return count

def update_junction_counts():
    for j in junctions:
        real_count = count_users_near_junction(j['lat'], j['lng'])
        if real_count > 0:
            j['vehicles'] = real_count
        else:
            j['vehicles'] = max(0, j['vehicles'] + random.randint(-1, 2))

def get_active_user_count():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM active_users WHERE last_seen >= datetime('now', '-30 seconds')")
    result = cursor.fetchone()
    conn.close()
    return result['cnt'] if result else 0

# ── DB Helpers ─────────────────────────────────────────────────
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
        (junction['id'], junction['name'], junction['vehicles'],
         junction['wait_time'], junction['status'], datetime.now().isoformat())
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
@app.post("/auth/login")
def login(data: dict):
    username = data.get("username", "")
    password = data.get("password", "")
    role = data.get("role", "driver")
    
    if role == "police":
        if password == "105": # Secret number for traffic police
            return {"message": "Login successful", "role": role, "user_id": username}
    elif role in ["driver", "ambulance", "fire"]:
        if len(username) > 0 and len(password) >= 4: # Driving License and DOB validation
            return {"message": "Login successful", "role": role, "user_id": username}
            
    return {"error": "Invalid credentials"}

@app.get("/")
def root():
    active_users = get_active_user_count()
    return {
        "message": "Smart Traffic API v3.0 — Tamil Nadu Coverage!",
        "ai_loaded": bool(ai_models),
        "version": "3.0.0",
        "active_users": active_users,
        "total_junctions": len(junctions),
        "coverage": "Tamil Nadu — 60 junctions across 15 cities",
    }

@app.post("/users/location")
def update_location(data: dict):
    user_id = data.get("user_id", "unknown")
    role = data.get("role", "driver")
    lat = data.get("lat", 0.0)
    lng = data.get("lng", 0.0)

    if lat == 0.0 and lng == 0.0:
        return {"error": "Invalid coordinates"}

    update_user_location(user_id, role, lat, lng)
    update_junction_counts()

    # Find nearest junction
    nearest = None
    min_dist = float('inf')
    for j in junctions:
        dist = haversine(lat, lng, j['lat'], j['lng'])
        if dist < min_dist:
            min_dist = dist
            nearest = j

    return {
        "message": "Location updated!",
        "user_id": user_id,
        "role": role,
        "nearest_junction": nearest['name'] if nearest else "Unknown",
        "distance_meters": round(min_dist),
        "active_users": get_active_user_count()
    }

@app.get("/users/active")
def get_active_users():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, role, lat, lng, last_seen FROM active_users WHERE last_seen >= datetime('now', '-30 seconds')")
    users = cursor.fetchall()
    conn.close()
    return {
        "active_users": [dict(u) for u in users],
        "total": len(users)
    }

# ── NEW: Search Junctions by Name or City ─────────────────────
@app.get("/junctions/search")
def search_junctions(q: str = ""):
    if not q:
        return {"junctions": junctions, "total": len(junctions)}
    query = q.lower()
    results = [j for j in junctions if query in j['name'].lower()]
    return {"junctions": results, "total": len(results)}

@app.get("/junctions")
def get_junctions():
    update_junction_counts()
    return {"junctions": junctions, "total": len(junctions)}

@app.get("/junctions/{junction_id}")
def get_junction(junction_id: int):
    for j in junctions:
        if j["id"] == junction_id:
            return j
    return {"error": "Junction not found"}

@app.post("/junctions/{junction_id}/override")
def override_junction(junction_id: int, data: dict):
    status = data.get("status", "green")
    road = data.get("road")
    for j in junctions:
        if j["id"] == junction_id:
            if road and road in j["roads"]:
                for r in j["roads"]:
                    j["roads"][r] = "red"
                j["roads"][road] = status
                j["status"] = "green" if status == "green" else "red"
                j["wait_time"] = 0
                return {"message": f"Road {road} at junction {junction_id} overridden to {status}", "junction": j}
            else:
                j["status"] = status
                j["wait_time"] = 0 if status == "green" else (60 if status == "red" else 30)
                return {"message": f"Junction {junction_id} overridden to {status}", "junction": j}
    return {"error": "Junction not found"}

@app.get("/traffic/summary")
def get_traffic_summary():
    update_junction_counts()
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
        "active_app_users": get_active_user_count(),
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
        "accuracy": "77.4%",
        "last_trained": "2026-03-19",
    }

# ── Emergency Endpoints ────────────────────────────────────────
@app.post("/emergency/activate")
def activate_emergency(data: dict):
    vehicle_type = data.get("vehicle_type", "Ambulance")
    route_ids = data.get("route_ids", [])
    emergency_active["active"] = True
    emergency_active["vehicle_type"] = vehicle_type
    emergency_active["activated_at"] = datetime.now().isoformat()
    emergency_active["route_ids"] = route_ids
    for j in junctions:
        if not route_ids or j["id"] in route_ids:
            j["status"] = "green"
            j["wait_time"] = 0
    db_log_emergency(vehicle_type, "activated")
    return {"message": f"{vehicle_type} emergency corridor activated!", "all_signals": "GREEN", "junctions_cleared": len(route_ids) if route_ids else len(junctions)}

@app.post("/emergency/deactivate")
def deactivate_emergency():
    vehicle_type = emergency_active.get("vehicle_type", "Unknown")
    emergency_active["active"] = False
    emergency_active["vehicle_type"] = None
    emergency_active["route_ids"] = []
    statuses = ["red", "green", "amber"]
    for j in junctions:
        j["status"] = random.choice(statuses)
        j["wait_time"] = random.randint(10, 60)
    db_log_emergency(vehicle_type, "deactivated")
    return {"message": "Emergency deactivated. Signals returned to normal."}

@app.post("/emergency/notify")
def notify_emergency(data: dict):
    user_id = data.get("user_id", "unknown")
    location = data.get("location", "Unknown Location")
    junction_id = data.get("junction_id")
    road = data.get("road")
    
    if junction_id and road:
        for j in junctions:
            if j["id"] == junction_id:
                if road not in j.get("emergency_alerts", []):
                    j.setdefault("emergency_alerts", []).append(road)
                return {"message": f"Alert sent for {road} road at {j['name']}!"}
                
    return {"message": "Notification sent to nearby vehicles to give space!"}

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

# ── History & Analytics ────────────────────────────────────────
@app.get("/history/traffic")
def get_traffic_history(junction_id: int = None, limit: int = 50):
    conn = get_db()
    cursor = conn.cursor()
    if junction_id:
        cursor.execute("SELECT * FROM traffic_logs WHERE junction_id=? ORDER BY logged_at DESC LIMIT ?", (junction_id, limit))
    else:
        cursor.execute("SELECT * FROM traffic_logs ORDER BY logged_at DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return {"history": [dict(row) for row in rows], "total": len(rows)}

@app.get("/history/stats")
def get_history_stats():
    return db_get_stats()

@app.get("/route/suggest")
def suggest_route():
    return {
        "current_route": {"name": "Main Street -> Park Road", "time_minutes": 18, "distance_km": 4.2, "congestion": "high"},
        "suggested_route": {"name": "College Road -> Market Square", "time_minutes": 11, "distance_km": 3.8, "congestion": "low"},
        "time_saved_minutes": 7,
    }

@app.get("/analytics/summary")
def get_analytics():
    update_junction_counts()
    busiest = max(junctions, key=lambda j: j['vehicles'])
    return {
        "peak_hour": "5:00 PM",
        "busiest_junction": busiest['name'],
        "avg_wait_time_seconds": sum(j['wait_time'] for j in junctions) // len(junctions),
        "emergency_response_time_minutes": 4.2,
        "total_junctions": len(junctions),
        "hourly_data": [
            {"hour": "8am", "value": 0.4}, {"hour": "9am", "value": 0.7},
            {"hour": "10am", "value": 0.5}, {"hour": "12pm", "value": 0.6},
            {"hour": "2pm", "value": 0.45}, {"hour": "4pm", "value": 0.8},
            {"hour": "5pm", "value": 1.0}, {"hour": "6pm", "value": 0.75},
        ],
        "congestion_by_junction": [
            {"junction": j['name'].split(',')[0], "percent": min(100, j['vehicles'] * 10 + 30)}
            for j in sorted(junctions, key=lambda x: x['vehicles'], reverse=True)[:10]
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
            update_junction_counts()
            for j in junctions:
                is_emergency_route = emergency_active["active"] and (not emergency_active.get("route_ids") or j["id"] in emergency_active.get("route_ids", []))
                if not is_emergency_route:
                    j["wait_time"] = max(5, j["wait_time"] + random.randint(-5, 5))
                    if j["vehicles"] == 0:
                        j["vehicles"] = max(0, j["vehicles"] + random.randint(-1, 3))

            log_counter += 1
            if log_counter % 10 == 0:
                for j in junctions:
                    db_log_traffic(j)

            await websocket.send_text(json.dumps({
                "type": "junction_update",
                "junctions": junctions,
                "active_users": get_active_user_count(),
                "timestamp": datetime.now().isoformat(),
                "emergency_active": emergency_active,
            }))
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        connected_clients.remove(websocket)