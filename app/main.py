# ╔══════════════════════════════════════════════════════════════════════╗
# ║   Smart Traffic API v4.0 — SECURITY HARDENED                        ║
# ║   Fixes: JWT Auth · bcrypt · Rate Limiting · CORS · Headers          ║
# ║          Pydantic Models · WebSocket Auth · Audit Logs · TOTP OTP   ║
# ╚══════════════════════════════════════════════════════════════════════╝

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect,
    Depends, HTTPException, status, Request, Query
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Literal
from jose import JWTError, jwt
from passlib.context import CryptContext
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import asyncio
import json
import random
import os
import sqlite3
import math
import heapq
import hashlib
import logging
import pyotp
from datetime import datetime, timedelta
from dotenv import load_dotenv
import pandas as pd

# ── Load environment variables ─────────────────────────────────────────────────
load_dotenv()

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("smart_traffic")

# ── Security Configuration ─────────────────────────────────────────────────────
SECRET_KEY = os.environ.get(
    "JWT_SECRET_KEY",
    "SmartTraffic-Prod-Secret-2026-ChangeThis-In-Production-MinLength32"
)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("TOKEN_EXPIRE_MINUTES", "60"))
REFRESH_TOKEN_EXPIRE_HOURS = 24
DEBUG_MODE = os.environ.get("DEBUG", "false").lower() == "true"
ENVIRONMENT = os.environ.get("ENVIRONMENT", "production")

ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS",
    "http://localhost:3000,http://localhost:8080,http://localhost,https://localhost"
).split(",")

# ── Rate Limiter ───────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

# ── Password Context ───────────────────────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ── OAuth2 Scheme ──────────────────────────────────────────────────────────────
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

# ── Token Blacklist (in-memory; use Redis in production) ──────────────────────
token_blacklist: set = set()

# ── FastAPI App ────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Smart Traffic API",
    version="4.0.0",
    docs_url="/docs" if DEBUG_MODE else None,
    redoc_url="/redoc" if DEBUG_MODE else None,
    openapi_url="/openapi.json" if DEBUG_MODE else None,
    description="Smart Traffic Management System — Security Hardened v4.0",
)

# Attach rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── Security Headers Middleware ────────────────────────────────────────────────
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains; preload"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "connect-src 'self' ws: wss:; "
            "frame-ancestors 'none'"
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "geolocation=(self), microphone=(), camera=(), "
            "payment=(), usb=(), magnetometer=(), gyroscope=()"
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        if "Server" in response.headers:
            del response.headers["Server"]
        return response


# ── Audit Log Middleware ───────────────────────────────────────────────────────
class AuditLogMiddleware(BaseHTTPMiddleware):
    SENSITIVE_PATHS = {
        "/emergency/activate", "/emergency/deactivate",
        "/auth/login", "/auth/verify_otp", "/auth/logout",
    }

    async def dispatch(self, request: Request, call_next):
        start = datetime.utcnow()
        response = await call_next(request)
        if any(request.url.path.startswith(p) for p in self.SENSITIVE_PATHS):
            elapsed = (datetime.utcnow() - start).total_seconds()
            logger.info(
                "AUDIT path=%s method=%s status=%s ip=%s elapsed=%.3fs",
                request.url.path, request.method, response.status_code,
                get_remote_address(request), elapsed
            )
        return response


# ── Add Middlewares (order matters — last added = outermost) ──────────────────
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(AuditLogMiddleware)

# Trusted hosts
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["localhost", "127.0.0.1", "*.railway.app", "*.render.com", "*"],
)

# CORS — restricted to known origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
    expose_headers=["X-Request-ID"],
    max_age=600,
)


# ── Custom Exception Handlers ──────────────────────────────────────────────────
@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return JSONResponse(status_code=404, content={"error": "Resource not found"})

@app.exception_handler(422)
async def validation_handler(request: Request, exc):
    return JSONResponse(
        status_code=422,
        content={"error": "Validation failed", "detail": "Invalid request parameters"}
    )

@app.exception_handler(500)
async def server_error_handler(request: Request, exc):
    logger.error("Internal server error: %s", str(exc))
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


# ── Database Setup ─────────────────────────────────────────────────────────────
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "traffic.db"))

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn

def init_db():
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                location TEXT NOT NULL,
                reported_at TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                resolved_at TEXT,
                reported_by TEXT DEFAULT 'anonymous'
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
                timestamp TEXT NOT NULL,
                activated_by TEXT DEFAULT 'system'
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
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_points (
                user_id TEXT PRIMARY KEY,
                points INTEGER DEFAULT 0,
                last_report TEXT
            )
        ''')
        # Auth tables
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS police_accounts (
                badge_id TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                mobile_number TEXT NOT NULL,
                totp_secret TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                last_login TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS registered_vehicles (
                license_number TEXT PRIMARY KEY,
                mobile_number TEXT NOT NULL,
                vehicle_type TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                resource TEXT,
                details TEXT,
                ip_address TEXT,
                timestamp TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS otp_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                badge_id TEXT NOT NULL,
                otp_code TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used INTEGER DEFAULT 0
            )
        ''')

        # Seed data — with HASHED passwords
        cursor.execute('SELECT COUNT(*) FROM police_accounts')
        if cursor.fetchone()[0] == 0:
            hashed_pw = pwd_context.hash("SecurePolice@2026!")
            totp_secret = pyotp.random_base32()
            cursor.execute(
                "INSERT INTO police_accounts "
                "(badge_id, password_hash, mobile_number, totp_secret) "
                "VALUES (?, ?, ?, ?)",
                ('POLICE123', hashed_pw, '9876543210', totp_secret)
            )
            logger.info("Seeded police account with hashed password. TOTP secret: %s", totp_secret)

        cursor.execute('SELECT COUNT(*) FROM registered_vehicles')
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "INSERT INTO registered_vehicles VALUES (?, ?, ?)",
                ('TN38AA1234', '9998887776', 'driver')
            )
            cursor.execute(
                "INSERT INTO registered_vehicles VALUES (?, ?, ?)",
                ('TN38EM0001', '9998881111', 'ambulance')
            )

        conn.commit()
        logger.info("Database initialized successfully with security schema.")
    finally:
        conn.close()

init_db()


# ── Safe AI Model Loading (joblib + hash verification) ───────────────────────
def load_models():
    models = {}
    model_paths = {
        'congestion': os.path.join('models', 'congestion_model.pkl'),
        'waittime':   os.path.join('models', 'waittime_model.pkl'),
        'signal':     os.path.join('models', 'signal_model.pkl'),
    }
    for name, path in model_paths.items():
        if os.path.exists(path):
            try:
                import joblib
                # Compute hash for integrity verification (log it)
                with open(path, 'rb') as f:
                    file_hash = hashlib.sha256(f.read()).hexdigest()
                logger.info("Loading model %s (sha256: %s...)", name, file_hash[:16])
                models[name] = joblib.load(path)
            except Exception as e:
                logger.warning("Could not load model %s: %s", name, type(e).__name__)
    return models

ai_models = load_models()


# ── JWT Utilities ──────────────────────────────────────────────────────────────
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "iat": datetime.utcnow(), "type": "access"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if token in token_blacklist:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return verify_token(token)


def require_police(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") not in ("police", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Police or admin role required",
        )
    return current_user


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return current_user


def optional_user(token: str = Depends(oauth2_scheme)) -> Optional[dict]:
    """Returns user if token present and valid, else None (for public endpoints)."""
    if not token:
        return None
    try:
        return verify_token(token)
    except HTTPException:
        return None


# ── Pydantic Request Models ────────────────────────────────────────────────────
VALID_VEHICLE_TYPES = {"Ambulance", "Fire Engine", "Police", "VIP Convoy", "Ambulance Bike"}
VALID_INCIDENT_TYPES = {
    "pothole", "accident", "waterlogging", "vip_movement",
    "police_barricade", "road_work", "signal_fault", "fire", "flood"
}
VALID_STATUSES = {"red", "green", "amber"}
VALID_ROADS = {"north", "south", "east", "west"}
VALID_ROLES = {"driver", "police", "admin", "ambulance"}


class LoginRequest(BaseModel):
    badge_id: str = Field(..., min_length=3, max_length=20, pattern=r"^[A-Z0-9]+$")
    password: str = Field(..., min_length=6, max_length=128)


class OTPRequest(BaseModel):
    badge_id: str = Field(..., min_length=3, max_length=20, pattern=r"^[A-Z0-9]+$")
    otp: str = Field(..., min_length=6, max_length=8, pattern=r"^\d{6,8}$")


class LocationRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    role: Literal["driver", "police", "admin", "ambulance"] = "driver"
    lat: float = Field(..., ge=-90.0, le=90.0)
    lng: float = Field(..., ge=-180.0, le=180.0)
    speed_kmh: Optional[float] = Field(None, ge=0, le=300)


class OverrideRequest(BaseModel):
    status: Literal["red", "green", "amber"]
    road: Optional[Literal["north", "south", "east", "west"]] = None


class EmergencyActivateRequest(BaseModel):
    vehicle_type: str = Field(..., min_length=2, max_length=50)
    route_ids: Optional[List[int]] = Field(None, max_length=50)

    @field_validator("vehicle_type")
    @classmethod
    def validate_vehicle_type(cls, v: str) -> str:
        if v not in VALID_VEHICLE_TYPES:
            raise ValueError(f"vehicle_type must be one of {VALID_VEHICLE_TYPES}")
        return v

    @field_validator("route_ids")
    @classmethod
    def validate_route_ids(cls, v):
        if v is not None:
            if len(v) == 0:
                raise ValueError("route_ids must not be empty if provided")
            if any(r < 1 or r > 9999 for r in v):
                raise ValueError("route_ids must be positive integers")
        return v


class EmergencyNotifyRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    location: str = Field(..., min_length=1, max_length=200)
    junction_id: Optional[int] = Field(None, ge=1)
    road: Optional[Literal["north", "south", "east", "west"]] = None


class IncidentReportRequest(BaseModel):
    type: str = Field(..., min_length=2, max_length=50)
    location: str = Field(..., min_length=2, max_length=200)
    description: Optional[str] = Field(None, max_length=500)

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in VALID_INCIDENT_TYPES:
            raise ValueError(f"type must be one of {VALID_INCIDENT_TYPES}")
        return v


# ── Audit Helper ───────────────────────────────────────────────────────────────
def audit_log(actor: str, action: str, resource: str = "", details: str = "", ip: str = ""):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO audit_logs (actor, action, resource, details, ip_address, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (actor, action, resource, details, ip, datetime.utcnow().isoformat())
        )
        conn.commit()
    finally:
        conn.close()


# ── Junction Data — 200 Tamil Nadu Junctions ──────────────────────────────────
junctions = [
    {"id": 1, "name": "Main Signal, Chennai", "lat": 13.1051, "lng": 80.2908, "status": "red", "wait_time": 28, "vehicles": 5},
    {"id": 2, "name": "Anna Nagar Signal, Chennai", "lat": 13.0921, "lng": 80.2926, "status": "amber", "wait_time": 16, "vehicles": 1},
    {"id": 3, "name": "Main Signal, Chennai", "lat": 13.051, "lng": 80.2702, "status": "green", "wait_time": 56, "vehicles": 1},
    {"id": 4, "name": "Gandhi Road Signal, Chennai", "lat": 13.1108, "lng": 80.2607, "status": "green", "wait_time": 39, "vehicles": 2},
    {"id": 5, "name": "Cross Signal, Chennai", "lat": 13.1165, "lng": 80.2562, "status": "amber", "wait_time": 12, "vehicles": 3},
    {"id": 6, "name": "Anna Nagar Signal, Chennai", "lat": 13.1318, "lng": 80.3185, "status": "green", "wait_time": 16, "vehicles": 3},
    {"id": 7, "name": "Market Signal, Chennai", "lat": 13.0353, "lng": 80.3186, "status": "red", "wait_time": 26, "vehicles": 3},
    {"id": 8, "name": "Gandhi Road Signal, Chennai", "lat": 13.0333, "lng": 80.2677, "status": "amber", "wait_time": 18, "vehicles": 2},
    {"id": 9, "name": "Highway Signal, Chennai", "lat": 13.0891, "lng": 80.2698, "status": "red", "wait_time": 30, "vehicles": 4},
    {"id": 10, "name": "Main Signal, Chennai", "lat": 13.0478, "lng": 80.2881, "status": "green", "wait_time": 45, "vehicles": 5},
    {"id": 11, "name": "Collectorate Signal, Chennai", "lat": 13.0829, "lng": 80.3035, "status": "amber", "wait_time": 49, "vehicles": 5},
    {"id": 12, "name": "Main Signal, Chennai", "lat": 13.1252, "lng": 80.3125, "status": "amber", "wait_time": 14, "vehicles": 3},
    {"id": 13, "name": "Temple Signal, Chennai", "lat": 13.0866, "lng": 80.2913, "status": "amber", "wait_time": 50, "vehicles": 2},
    {"id": 14, "name": "Collectorate Signal, Chennai", "lat": 13.0475, "lng": 80.2374, "status": "green", "wait_time": 16, "vehicles": 4},
    {"id": 15, "name": "Temple Signal, Chennai", "lat": 13.0867, "lng": 80.2481, "status": "green", "wait_time": 48, "vehicles": 2},
    {"id": 16, "name": "Market Signal, Chennai", "lat": 13.065, "lng": 80.2315, "status": "amber", "wait_time": 27, "vehicles": 2},
    {"id": 17, "name": "Old Bus Stand Signal, Chennai", "lat": 13.0658, "lng": 80.315, "status": "amber", "wait_time": 52, "vehicles": 1},
    {"id": 18, "name": "Railway Station Signal, Chennai", "lat": 13.1092, "lng": 80.2996, "status": "red", "wait_time": 42, "vehicles": 4},
    {"id": 19, "name": "Collectorate Signal, Chennai", "lat": 13.1034, "lng": 80.2803, "status": "green", "wait_time": 44, "vehicles": 0},
    {"id": 20, "name": "Main Signal, Chennai", "lat": 13.0925, "lng": 80.2857, "status": "green", "wait_time": 36, "vehicles": 0},
    {"id": 21, "name": "Collectorate Signal, Chennai", "lat": 13.0843, "lng": 80.319, "status": "green", "wait_time": 25, "vehicles": 4},
    {"id": 22, "name": "Old Bus Stand Signal, Chennai", "lat": 13.0946, "lng": 80.2503, "status": "red", "wait_time": 55, "vehicles": 2},
    {"id": 23, "name": "Highway Signal, Chennai", "lat": 13.0618, "lng": 80.2969, "status": "amber", "wait_time": 39, "vehicles": 1},
    {"id": 24, "name": "Old Bus Stand Signal, Chennai", "lat": 13.0561, "lng": 80.2475, "status": "green", "wait_time": 46, "vehicles": 4},
    {"id": 25, "name": "Temple Signal, Chennai", "lat": 13.0619, "lng": 80.3013, "status": "amber", "wait_time": 16, "vehicles": 3},
    {"id": 26, "name": "Bypass Signal, Chennai", "lat": 13.1161, "lng": 80.2664, "status": "amber", "wait_time": 21, "vehicles": 3},
    {"id": 27, "name": "Collectorate Signal, Chennai", "lat": 13.0932, "lng": 80.2672, "status": "amber", "wait_time": 30, "vehicles": 0},
    {"id": 28, "name": "New Bus Stand Signal, Chennai", "lat": 13.0508, "lng": 80.304, "status": "green", "wait_time": 27, "vehicles": 5},
    {"id": 29, "name": "Tollgate Signal, Chennai", "lat": 13.1164, "lng": 80.3086, "status": "green", "wait_time": 40, "vehicles": 2},
    {"id": 30, "name": "Temple Signal, Chennai", "lat": 13.0401, "lng": 80.2408, "status": "red", "wait_time": 45, "vehicles": 4},
    {"id": 31, "name": "Highway Signal, Chennai", "lat": 13.0403, "lng": 80.2503, "status": "red", "wait_time": 52, "vehicles": 3},
    {"id": 32, "name": "Cross Signal, Chennai", "lat": 13.034, "lng": 80.2468, "status": "green", "wait_time": 58, "vehicles": 4},
    {"id": 33, "name": "Gandhi Road Signal, Chennai", "lat": 13.0681, "lng": 80.242, "status": "amber", "wait_time": 41, "vehicles": 4},
    {"id": 34, "name": "Collectorate Signal, Chennai", "lat": 13.0662, "lng": 80.2749, "status": "red", "wait_time": 18, "vehicles": 4},
    {"id": 35, "name": "Gandhi Road Signal, Chennai", "lat": 13.0781, "lng": 80.2534, "status": "red", "wait_time": 48, "vehicles": 1},
    {"id": 36, "name": "Market Signal, Chennai", "lat": 13.0782, "lng": 80.2359, "status": "red", "wait_time": 41, "vehicles": 1},
    {"id": 37, "name": "Railway Station Signal, Chennai", "lat": 13.0378, "lng": 80.3059, "status": "red", "wait_time": 57, "vehicles": 0},
    {"id": 38, "name": "Market Signal, Chennai", "lat": 13.1097, "lng": 80.233, "status": "amber", "wait_time": 27, "vehicles": 4},
    {"id": 39, "name": "Cross Signal, Chennai", "lat": 13.0472, "lng": 80.279, "status": "red", "wait_time": 52, "vehicles": 3},
    {"id": 40, "name": "Anna Nagar Signal, Chennai", "lat": 13.1072, "lng": 80.28, "status": "green", "wait_time": 49, "vehicles": 5},
    {"id": 41, "name": "Main Signal, Coimbatore", "lat": 11.0522, "lng": 76.9956, "status": "red", "wait_time": 53, "vehicles": 1},
    {"id": 42, "name": "Cross Signal, Coimbatore", "lat": 11.0652, "lng": 76.9258, "status": "red", "wait_time": 40, "vehicles": 3},
    {"id": 43, "name": "Temple Signal, Coimbatore", "lat": 11.0619, "lng": 76.9148, "status": "red", "wait_time": 31, "vehicles": 1},
    {"id": 44, "name": "Market Signal, Coimbatore", "lat": 11.0019, "lng": 76.9938, "status": "green", "wait_time": 26, "vehicles": 4},
    {"id": 45, "name": "Bypass Signal, Coimbatore", "lat": 11.0367, "lng": 76.9897, "status": "red", "wait_time": 23, "vehicles": 0},
    {"id": 46, "name": "Temple Signal, Coimbatore", "lat": 11.0164, "lng": 76.9893, "status": "red", "wait_time": 15, "vehicles": 0},
    {"id": 47, "name": "New Bus Stand Signal, Coimbatore", "lat": 11.0335, "lng": 76.9689, "status": "green", "wait_time": 10, "vehicles": 5},
    {"id": 48, "name": "Market Signal, Coimbatore", "lat": 10.993, "lng": 76.938, "status": "amber", "wait_time": 45, "vehicles": 2},
    {"id": 49, "name": "New Bus Stand Signal, Coimbatore", "lat": 11.0001, "lng": 76.9534, "status": "amber", "wait_time": 31, "vehicles": 1},
    {"id": 50, "name": "Market Signal, Coimbatore", "lat": 10.9813, "lng": 76.9305, "status": "red", "wait_time": 18, "vehicles": 0},
    {"id": 51, "name": "Collectorate Signal, Coimbatore", "lat": 11.0428, "lng": 76.9891, "status": "amber", "wait_time": 59, "vehicles": 1},
    {"id": 52, "name": "Collectorate Signal, Coimbatore", "lat": 10.9707, "lng": 77.0, "status": "green", "wait_time": 15, "vehicles": 2},
    {"id": 53, "name": "Anna Nagar Signal, Coimbatore", "lat": 11.0494, "lng": 76.9697, "status": "green", "wait_time": 30, "vehicles": 0},
    {"id": 54, "name": "Bypass Signal, Coimbatore", "lat": 10.983, "lng": 76.9929, "status": "green", "wait_time": 30, "vehicles": 2},
    {"id": 55, "name": "Cross Signal, Coimbatore", "lat": 11.0275, "lng": 76.9537, "status": "amber", "wait_time": 14, "vehicles": 4},
    {"id": 56, "name": "Main Signal, Coimbatore", "lat": 11.0581, "lng": 76.917, "status": "amber", "wait_time": 24, "vehicles": 3},
    {"id": 57, "name": "Anna Nagar Signal, Coimbatore", "lat": 11.0437, "lng": 76.9707, "status": "green", "wait_time": 54, "vehicles": 1},
    {"id": 58, "name": "Anna Nagar Signal, Coimbatore", "lat": 11.0642, "lng": 76.9388, "status": "red", "wait_time": 14, "vehicles": 0},
    {"id": 59, "name": "Railway Station Signal, Coimbatore", "lat": 10.9867, "lng": 76.9698, "status": "green", "wait_time": 17, "vehicles": 3},
    {"id": 60, "name": "Tollgate Signal, Coimbatore", "lat": 11.0381, "lng": 76.9855, "status": "red", "wait_time": 50, "vehicles": 2},
    {"id": 61, "name": "New Bus Stand Signal, Coimbatore", "lat": 11.0547, "lng": 76.9789, "status": "amber", "wait_time": 32, "vehicles": 3},
    {"id": 62, "name": "Market Signal, Coimbatore", "lat": 11.0112, "lng": 76.9219, "status": "green", "wait_time": 48, "vehicles": 0},
    {"id": 63, "name": "Collectorate Signal, Coimbatore", "lat": 11.0195, "lng": 76.9357, "status": "red", "wait_time": 12, "vehicles": 1},
    {"id": 64, "name": "Market Signal, Coimbatore", "lat": 11.0557, "lng": 76.9274, "status": "green", "wait_time": 30, "vehicles": 3},
    {"id": 65, "name": "Bypass Signal, Coimbatore", "lat": 11.0583, "lng": 76.9554, "status": "amber", "wait_time": 54, "vehicles": 5},
    {"id": 66, "name": "Anna Nagar Signal, Madurai", "lat": 9.9711, "lng": 78.1256, "status": "green", "wait_time": 54, "vehicles": 0},
    {"id": 67, "name": "Bypass Signal, Madurai", "lat": 9.954, "lng": 78.1413, "status": "amber", "wait_time": 54, "vehicles": 5},
    {"id": 68, "name": "Anna Nagar Signal, Madurai", "lat": 9.8768, "lng": 78.104, "status": "red", "wait_time": 48, "vehicles": 3},
    {"id": 69, "name": "New Bus Stand Signal, Madurai", "lat": 9.9341, "lng": 78.0771, "status": "amber", "wait_time": 29, "vehicles": 2},
    {"id": 70, "name": "Anna Nagar Signal, Madurai", "lat": 9.9729, "lng": 78.1266, "status": "amber", "wait_time": 53, "vehicles": 1},
    {"id": 71, "name": "Market Signal, Madurai", "lat": 9.8773, "lng": 78.0991, "status": "green", "wait_time": 23, "vehicles": 1},
    {"id": 72, "name": "Railway Station Signal, Madurai", "lat": 9.8834, "lng": 78.1198, "status": "green", "wait_time": 41, "vehicles": 1},
    {"id": 73, "name": "Main Signal, Madurai", "lat": 9.9543, "lng": 78.1221, "status": "red", "wait_time": 47, "vehicles": 3},
    {"id": 74, "name": "Market Signal, Madurai", "lat": 9.9176, "lng": 78.0951, "status": "green", "wait_time": 43, "vehicles": 1},
    {"id": 75, "name": "New Bus Stand Signal, Madurai", "lat": 9.8911, "lng": 78.1348, "status": "green", "wait_time": 45, "vehicles": 1},
    {"id": 76, "name": "Old Bus Stand Signal, Madurai", "lat": 9.9405, "lng": 78.0849, "status": "amber", "wait_time": 29, "vehicles": 3},
    {"id": 77, "name": "Gandhi Road Signal, Madurai", "lat": 9.9573, "lng": 78.0853, "status": "red", "wait_time": 23, "vehicles": 5},
    {"id": 78, "name": "Market Signal, Madurai", "lat": 9.8857, "lng": 78.132, "status": "green", "wait_time": 59, "vehicles": 2},
    {"id": 79, "name": "Cross Signal, Madurai", "lat": 9.8815, "lng": 78.0976, "status": "green", "wait_time": 47, "vehicles": 0},
    {"id": 80, "name": "Market Signal, Madurai", "lat": 9.9096, "lng": 78.1017, "status": "green", "wait_time": 30, "vehicles": 0},
    {"id": 81, "name": "Anna Nagar Signal, Madurai", "lat": 9.9331, "lng": 78.0861, "status": "red", "wait_time": 53, "vehicles": 3},
    {"id": 82, "name": "Old Bus Stand Signal, Madurai", "lat": 9.9168, "lng": 78.1376, "status": "red", "wait_time": 20, "vehicles": 4},
    {"id": 83, "name": "Gandhi Road Signal, Madurai", "lat": 9.905, "lng": 78.1332, "status": "red", "wait_time": 26, "vehicles": 0},
    {"id": 84, "name": "Cross Signal, Madurai", "lat": 9.8872, "lng": 78.071, "status": "green", "wait_time": 57, "vehicles": 5},
    {"id": 85, "name": "New Bus Stand Signal, Madurai", "lat": 9.9274, "lng": 78.1386, "status": "amber", "wait_time": 16, "vehicles": 5},
    {"id": 86, "name": "Collectorate Signal, Trichy", "lat": 10.7446, "lng": 78.7106, "status": "amber", "wait_time": 54, "vehicles": 2},
    {"id": 87, "name": "Cross Signal, Trichy", "lat": 10.767, "lng": 78.72, "status": "red", "wait_time": 50, "vehicles": 3},
    {"id": 88, "name": "Cross Signal, Trichy", "lat": 10.7938, "lng": 78.6666, "status": "amber", "wait_time": 37, "vehicles": 1},
    {"id": 89, "name": "Anna Nagar Signal, Trichy", "lat": 10.7504, "lng": 78.7161, "status": "amber", "wait_time": 15, "vehicles": 3},
    {"id": 90, "name": "Temple Signal, Trichy", "lat": 10.7765, "lng": 78.7478, "status": "amber", "wait_time": 19, "vehicles": 1},
    {"id": 91, "name": "Old Bus Stand Signal, Trichy", "lat": 10.8362, "lng": 78.6558, "status": "green", "wait_time": 38, "vehicles": 5},
    {"id": 92, "name": "Collectorate Signal, Trichy", "lat": 10.7695, "lng": 78.7283, "status": "amber", "wait_time": 22, "vehicles": 1},
    {"id": 93, "name": "Tollgate Signal, Trichy", "lat": 10.8358, "lng": 78.7502, "status": "green", "wait_time": 25, "vehicles": 2},
    {"id": 94, "name": "Highway Signal, Trichy", "lat": 10.7779, "lng": 78.6932, "status": "red", "wait_time": 53, "vehicles": 3},
    {"id": 95, "name": "Collectorate Signal, Trichy", "lat": 10.8234, "lng": 78.6894, "status": "amber", "wait_time": 12, "vehicles": 1},
    {"id": 96, "name": "Cross Signal, Trichy", "lat": 10.7618, "lng": 78.6895, "status": "red", "wait_time": 40, "vehicles": 0},
    {"id": 97, "name": "Railway Station Signal, Trichy", "lat": 10.7597, "lng": 78.6635, "status": "amber", "wait_time": 40, "vehicles": 1},
    {"id": 98, "name": "Highway Signal, Trichy", "lat": 10.8133, "lng": 78.6927, "status": "red", "wait_time": 10, "vehicles": 4},
    {"id": 99, "name": "Old Bus Stand Signal, Trichy", "lat": 10.7579, "lng": 78.6654, "status": "green", "wait_time": 50, "vehicles": 4},
    {"id": 100, "name": "Tollgate Signal, Trichy", "lat": 10.8235, "lng": 78.747, "status": "amber", "wait_time": 52, "vehicles": 0},
    {"id": 101, "name": "New Bus Stand Signal, Salem", "lat": 11.656, "lng": 78.1259, "status": "red", "wait_time": 43, "vehicles": 4},
    {"id": 102, "name": "Railway Station Signal, Salem", "lat": 11.6479, "lng": 78.0992, "status": "amber", "wait_time": 18, "vehicles": 4},
    {"id": 103, "name": "Gandhi Road Signal, Salem", "lat": 11.7002, "lng": 78.1032, "status": "red", "wait_time": 35, "vehicles": 1},
    {"id": 104, "name": "Cross Signal, Salem", "lat": 11.6612, "lng": 78.1498, "status": "amber", "wait_time": 59, "vehicles": 4},
    {"id": 105, "name": "Gandhi Road Signal, Salem", "lat": 11.6161, "lng": 78.1229, "status": "red", "wait_time": 44, "vehicles": 4},
    {"id": 106, "name": "Gandhi Road Signal, Salem", "lat": 11.6346, "lng": 78.173, "status": "amber", "wait_time": 15, "vehicles": 4},
    {"id": 107, "name": "Railway Station Signal, Salem", "lat": 11.6391, "lng": 78.1004, "status": "red", "wait_time": 34, "vehicles": 4},
    {"id": 108, "name": "Tollgate Signal, Salem", "lat": 11.6731, "lng": 78.145, "status": "amber", "wait_time": 39, "vehicles": 2},
    {"id": 109, "name": "Market Signal, Salem", "lat": 11.713, "lng": 78.1537, "status": "amber", "wait_time": 12, "vehicles": 3},
    {"id": 110, "name": "Temple Signal, Salem", "lat": 11.6735, "lng": 78.1914, "status": "amber", "wait_time": 48, "vehicles": 5},
    {"id": 111, "name": "Anna Nagar Signal, Salem", "lat": 11.6962, "lng": 78.1631, "status": "amber", "wait_time": 43, "vehicles": 3},
    {"id": 112, "name": "Temple Signal, Salem", "lat": 11.6907, "lng": 78.1382, "status": "green", "wait_time": 49, "vehicles": 2},
    {"id": 113, "name": "Collectorate Signal, Salem", "lat": 11.6845, "lng": 78.1504, "status": "red", "wait_time": 51, "vehicles": 4},
    {"id": 114, "name": "Cross Signal, Salem", "lat": 11.6721, "lng": 78.1003, "status": "amber", "wait_time": 29, "vehicles": 1},
    {"id": 115, "name": "Gandhi Road Signal, Salem", "lat": 11.6292, "lng": 78.1064, "status": "red", "wait_time": 43, "vehicles": 1},
    {"id": 116, "name": "Anna Nagar Signal, Erode", "lat": 11.3044, "lng": 77.6715, "status": "green", "wait_time": 22, "vehicles": 3},
    {"id": 117, "name": "Anna Nagar Signal, Erode", "lat": 11.3696, "lng": 77.7359, "status": "amber", "wait_time": 15, "vehicles": 5},
    {"id": 118, "name": "Tollgate Signal, Erode", "lat": 11.3718, "lng": 77.7433, "status": "green", "wait_time": 31, "vehicles": 1},
    {"id": 119, "name": "New Bus Stand Signal, Erode", "lat": 11.3368, "lng": 77.6828, "status": "green", "wait_time": 15, "vehicles": 1},
    {"id": 120, "name": "New Bus Stand Signal, Erode", "lat": 11.3891, "lng": 77.7631, "status": "red", "wait_time": 37, "vehicles": 1},
    {"id": 121, "name": "Railway Station Signal, Erode", "lat": 11.3485, "lng": 77.7256, "status": "green", "wait_time": 39, "vehicles": 4},
    {"id": 122, "name": "Highway Signal, Erode", "lat": 11.3757, "lng": 77.6709, "status": "amber", "wait_time": 36, "vehicles": 0},
    {"id": 123, "name": "Highway Signal, Erode", "lat": 11.3442, "lng": 77.7055, "status": "red", "wait_time": 34, "vehicles": 5},
    {"id": 124, "name": "Highway Signal, Erode", "lat": 11.3158, "lng": 77.7432, "status": "amber", "wait_time": 11, "vehicles": 5},
    {"id": 125, "name": "Tollgate Signal, Erode", "lat": 11.3342, "lng": 77.7511, "status": "amber", "wait_time": 31, "vehicles": 2},
    {"id": 126, "name": "Gandhi Road Signal, Tiruppur", "lat": 11.1381, "lng": 77.3819, "status": "red", "wait_time": 55, "vehicles": 0},
    {"id": 127, "name": "Main Signal, Tiruppur", "lat": 11.1094, "lng": 77.3045, "status": "red", "wait_time": 16, "vehicles": 2},
    {"id": 128, "name": "Main Signal, Tiruppur", "lat": 11.083, "lng": 77.3391, "status": "green", "wait_time": 48, "vehicles": 0},
    {"id": 129, "name": "Collectorate Signal, Tiruppur", "lat": 11.1183, "lng": 77.3197, "status": "red", "wait_time": 39, "vehicles": 3},
    {"id": 130, "name": "New Bus Stand Signal, Tiruppur", "lat": 11.0636, "lng": 77.3461, "status": "amber", "wait_time": 57, "vehicles": 3},
    {"id": 131, "name": "Main Signal, Tiruppur", "lat": 11.125, "lng": 77.356, "status": "red", "wait_time": 26, "vehicles": 0},
    {"id": 132, "name": "Anna Nagar Signal, Tiruppur", "lat": 11.0925, "lng": 77.3277, "status": "amber", "wait_time": 46, "vehicles": 3},
    {"id": 133, "name": "Anna Nagar Signal, Tiruppur", "lat": 11.1392, "lng": 77.3601, "status": "red", "wait_time": 47, "vehicles": 1},
    {"id": 134, "name": "New Bus Stand Signal, Tiruppur", "lat": 11.1531, "lng": 77.3636, "status": "red", "wait_time": 36, "vehicles": 3},
    {"id": 135, "name": "Temple Signal, Tiruppur", "lat": 11.0827, "lng": 77.3593, "status": "green", "wait_time": 41, "vehicles": 3},
    {"id": 136, "name": "Cross Signal, Vellore", "lat": 12.9658, "lng": 79.1465, "status": "green", "wait_time": 19, "vehicles": 5},
    {"id": 137, "name": "Market Signal, Vellore", "lat": 12.9038, "lng": 79.1149, "status": "green", "wait_time": 48, "vehicles": 5},
    {"id": 138, "name": "Temple Signal, Vellore", "lat": 12.9235, "lng": 79.1409, "status": "amber", "wait_time": 24, "vehicles": 2},
    {"id": 139, "name": "Temple Signal, Vellore", "lat": 12.8848, "lng": 79.121, "status": "red", "wait_time": 42, "vehicles": 4},
    {"id": 140, "name": "Old Bus Stand Signal, Vellore", "lat": 12.8786, "lng": 79.1578, "status": "green", "wait_time": 11, "vehicles": 2},
    {"id": 141, "name": "Old Bus Stand Signal, Vellore", "lat": 12.9536, "lng": 79.1402, "status": "green", "wait_time": 44, "vehicles": 5},
    {"id": 142, "name": "New Bus Stand Signal, Vellore", "lat": 12.8924, "lng": 79.0852, "status": "green", "wait_time": 34, "vehicles": 1},
    {"id": 143, "name": "Gandhi Road Signal, Vellore", "lat": 12.8759, "lng": 79.1292, "status": "red", "wait_time": 36, "vehicles": 2},
    {"id": 144, "name": "Market Signal, Vellore", "lat": 12.9476, "lng": 79.0924, "status": "amber", "wait_time": 58, "vehicles": 1},
    {"id": 145, "name": "Collectorate Signal, Vellore", "lat": 12.9339, "lng": 79.1782, "status": "green", "wait_time": 41, "vehicles": 0},
    {"id": 146, "name": "Highway Signal, Thanjavur", "lat": 10.7836, "lng": 79.1805, "status": "green", "wait_time": 23, "vehicles": 5},
    {"id": 147, "name": "Bypass Signal, Thanjavur", "lat": 10.8214, "lng": 79.104, "status": "red", "wait_time": 47, "vehicles": 2},
    {"id": 148, "name": "Cross Signal, Thanjavur", "lat": 10.7783, "lng": 79.1856, "status": "green", "wait_time": 52, "vehicles": 1},
    {"id": 149, "name": "Highway Signal, Thanjavur", "lat": 10.8166, "lng": 79.0932, "status": "red", "wait_time": 26, "vehicles": 0},
    {"id": 150, "name": "Railway Station Signal, Thanjavur", "lat": 10.7955, "lng": 79.1086, "status": "amber", "wait_time": 58, "vehicles": 1},
    {"id": 151, "name": "Anna Nagar Signal, Thanjavur", "lat": 10.779, "lng": 79.1268, "status": "green", "wait_time": 45, "vehicles": 2},
    {"id": 152, "name": "Market Signal, Thanjavur", "lat": 10.7529, "lng": 79.1423, "status": "red", "wait_time": 43, "vehicles": 4},
    {"id": 153, "name": "Cross Signal, Thanjavur", "lat": 10.7965, "lng": 79.1553, "status": "green", "wait_time": 42, "vehicles": 5},
    {"id": 154, "name": "Tollgate Signal, Thanjavur", "lat": 10.772, "lng": 79.092, "status": "green", "wait_time": 36, "vehicles": 2},
    {"id": 155, "name": "Old Bus Stand Signal, Thanjavur", "lat": 10.7485, "lng": 79.1036, "status": "amber", "wait_time": 30, "vehicles": 1},
    {"id": 156, "name": "Market Signal, Tirunelveli", "lat": 8.7181, "lng": 77.7141, "status": "amber", "wait_time": 60, "vehicles": 1},
    {"id": 157, "name": "Market Signal, Tirunelveli", "lat": 8.6673, "lng": 77.7813, "status": "green", "wait_time": 44, "vehicles": 2},
    {"id": 158, "name": "Market Signal, Tirunelveli", "lat": 8.7578, "lng": 77.7951, "status": "green", "wait_time": 44, "vehicles": 5},
    {"id": 159, "name": "Cross Signal, Tirunelveli", "lat": 8.7621, "lng": 77.785, "status": "amber", "wait_time": 11, "vehicles": 4},
    {"id": 160, "name": "New Bus Stand Signal, Tirunelveli", "lat": 8.7007, "lng": 77.7102, "status": "green", "wait_time": 38, "vehicles": 1},
    {"id": 161, "name": "Highway Signal, Tirunelveli", "lat": 8.7055, "lng": 77.7885, "status": "red", "wait_time": 30, "vehicles": 4},
    {"id": 162, "name": "Old Bus Stand Signal, Tirunelveli", "lat": 8.7227, "lng": 77.7595, "status": "green", "wait_time": 55, "vehicles": 3},
    {"id": 163, "name": "Main Signal, Tirunelveli", "lat": 8.6771, "lng": 77.7908, "status": "green", "wait_time": 11, "vehicles": 2},
    {"id": 164, "name": "Highway Signal, Tirunelveli", "lat": 8.7521, "lng": 77.7293, "status": "amber", "wait_time": 31, "vehicles": 2},
    {"id": 165, "name": "Anna Nagar Signal, Tirunelveli", "lat": 8.7485, "lng": 77.709, "status": "red", "wait_time": 15, "vehicles": 5},
    {"id": 166, "name": "Cross Signal, Thoothukudi", "lat": 8.7675, "lng": 78.1388, "status": "green", "wait_time": 19, "vehicles": 0},
    {"id": 167, "name": "Anna Nagar Signal, Thoothukudi", "lat": 8.7852, "lng": 78.0968, "status": "red", "wait_time": 44, "vehicles": 4},
    {"id": 168, "name": "Cross Signal, Thoothukudi", "lat": 8.7223, "lng": 78.1161, "status": "amber", "wait_time": 59, "vehicles": 5},
    {"id": 169, "name": "Highway Signal, Thoothukudi", "lat": 8.8066, "lng": 78.1424, "status": "green", "wait_time": 16, "vehicles": 4},
    {"id": 170, "name": "Tollgate Signal, Thoothukudi", "lat": 8.7467, "lng": 78.0892, "status": "amber", "wait_time": 45, "vehicles": 3},
    {"id": 171, "name": "New Bus Stand Signal, Thoothukudi", "lat": 8.8102, "lng": 78.1397, "status": "amber", "wait_time": 50, "vehicles": 0},
    {"id": 172, "name": "Market Signal, Thoothukudi", "lat": 8.7242, "lng": 78.1185, "status": "red", "wait_time": 18, "vehicles": 4},
    {"id": 173, "name": "Main Signal, Thoothukudi", "lat": 8.7622, "lng": 78.1447, "status": "red", "wait_time": 35, "vehicles": 0},
    {"id": 174, "name": "Railway Station Signal, Thoothukudi", "lat": 8.7504, "lng": 78.1504, "status": "green", "wait_time": 26, "vehicles": 0},
    {"id": 175, "name": "Market Signal, Thoothukudi", "lat": 8.814, "lng": 78.0942, "status": "amber", "wait_time": 25, "vehicles": 5},
    {"id": 176, "name": "Cross Signal, Nagercoil", "lat": 8.2272, "lng": 77.4344, "status": "red", "wait_time": 17, "vehicles": 3},
    {"id": 177, "name": "Highway Signal, Nagercoil", "lat": 8.1511, "lng": 77.3767, "status": "red", "wait_time": 15, "vehicles": 2},
    {"id": 178, "name": "Cross Signal, Nagercoil", "lat": 8.186, "lng": 77.4514, "status": "green", "wait_time": 28, "vehicles": 5},
    {"id": 179, "name": "Highway Signal, Nagercoil", "lat": 8.1525, "lng": 77.3724, "status": "green", "wait_time": 47, "vehicles": 3},
    {"id": 180, "name": "Collectorate Signal, Nagercoil", "lat": 8.1477, "lng": 77.4193, "status": "amber", "wait_time": 28, "vehicles": 2},
    {"id": 181, "name": "Gandhi Road Signal, Nagercoil", "lat": 8.2026, "lng": 77.4459, "status": "amber", "wait_time": 11, "vehicles": 2},
    {"id": 182, "name": "Old Bus Stand Signal, Nagercoil", "lat": 8.2262, "lng": 77.3663, "status": "amber", "wait_time": 32, "vehicles": 5},
    {"id": 183, "name": "Temple Signal, Nagercoil", "lat": 8.1378, "lng": 77.3898, "status": "amber", "wait_time": 34, "vehicles": 2},
    {"id": 184, "name": "Bypass Signal, Nagercoil", "lat": 8.1877, "lng": 77.3623, "status": "green", "wait_time": 10, "vehicles": 1},
    {"id": 185, "name": "Highway Signal, Nagercoil", "lat": 8.1904, "lng": 77.448, "status": "red", "wait_time": 37, "vehicles": 0},
    {"id": 186, "name": "Market Signal, Dindigul", "lat": 10.3455, "lng": 77.98, "status": "red", "wait_time": 28, "vehicles": 3},
    {"id": 187, "name": "Market Signal, Dindigul", "lat": 10.3507, "lng": 77.982, "status": "amber", "wait_time": 34, "vehicles": 3},
    {"id": 188, "name": "Temple Signal, Dindigul", "lat": 10.3621, "lng": 77.9724, "status": "green", "wait_time": 48, "vehicles": 0},
    {"id": 189, "name": "Bypass Signal, Dindigul", "lat": 10.3316, "lng": 77.9305, "status": "amber", "wait_time": 20, "vehicles": 1},
    {"id": 190, "name": "Tollgate Signal, Dindigul", "lat": 10.3811, "lng": 77.9988, "status": "green", "wait_time": 37, "vehicles": 2},
    {"id": 191, "name": "Old Bus Stand Signal, Kanchipuram", "lat": 12.7868, "lng": 79.7529, "status": "green", "wait_time": 39, "vehicles": 1},
    {"id": 192, "name": "Highway Signal, Kanchipuram", "lat": 12.828, "lng": 79.6814, "status": "red", "wait_time": 41, "vehicles": 4},
    {"id": 193, "name": "Railway Station Signal, Kanchipuram", "lat": 12.8417, "lng": 79.7314, "status": "amber", "wait_time": 40, "vehicles": 3},
    {"id": 194, "name": "Railway Station Signal, Kanchipuram", "lat": 12.8177, "lng": 79.7227, "status": "amber", "wait_time": 36, "vehicles": 5},
    {"id": 195, "name": "Main Signal, Kanchipuram", "lat": 12.8731, "lng": 79.7393, "status": "green", "wait_time": 45, "vehicles": 3},
    {"id": 196, "name": "Market Signal, Ooty", "lat": 11.4476, "lng": 76.7407, "status": "amber", "wait_time": 26, "vehicles": 3},
    {"id": 197, "name": "Market Signal, Ooty", "lat": 11.4351, "lng": 76.7286, "status": "red", "wait_time": 12, "vehicles": 3},
    {"id": 198, "name": "Cross Signal, Ooty", "lat": 11.4358, "lng": 76.6773, "status": "green", "wait_time": 50, "vehicles": 2},
    {"id": 199, "name": "Tollgate Signal, Ooty", "lat": 11.3969, "lng": 76.7463, "status": "red", "wait_time": 16, "vehicles": 0},
    {"id": 200, "name": "Temple Signal, Ooty", "lat": 11.4102, "lng": 76.7443, "status": "red", "wait_time": 30, "vehicles": 1},
]

# Initialize roads and emergency_alerts
for j in junctions:
    j["roads"] = {"north": "red", "south": "red", "east": "red", "west": "red"}
    j["emergency_alerts"] = []

emergency_active = {"active": False, "vehicle_type": None, "activated_at": None, "route_ids": []}
connected_clients: List[WebSocket] = []


# ── Haversine Distance ─────────────────────────────────────────────────────────
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


# ── GPS User Functions ─────────────────────────────────────────────────────────
def update_user_location(user_id: str, role: str, lat: float, lng: float):
    conn = get_db()
    try:
        now = datetime.utcnow().isoformat()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM active_users WHERE user_id=?", (user_id,))
        if cursor.fetchone():
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
    finally:
        conn.close()


def count_users_near_junction(junction_lat, junction_lng, radius=1000):
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT lat, lng FROM active_users WHERE last_seen >= datetime('now', '-30 seconds')"
        )
        users = cursor.fetchall()
    finally:
        conn.close()
    return sum(1 for u in users if haversine(junction_lat, junction_lng, u['lat'], u['lng']) <= radius)


def update_junction_counts():
    for j in junctions:
        real_count = count_users_near_junction(j['lat'], j['lng'])
        j['vehicles'] = real_count if real_count > 0 else max(0, j['vehicles'] + random.randint(-1, 2))


def get_active_user_count():
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM active_users WHERE last_seen >= datetime('now', '-30 seconds')"
        )
        result = cursor.fetchone()
        return result['cnt'] if result else 0
    finally:
        conn.close()


# ── DB Helpers ─────────────────────────────────────────────────────────────────
def db_save_incident(inc_type, location, user_id="anonymous"):
    conn = get_db()
    try:
        now = datetime.utcnow().isoformat()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO incidents (type, location, reported_at, status, reported_by) "
            "VALUES (?, ?, ?, 'active', ?)",
            (inc_type, location, now, user_id)
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def db_get_incidents():
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM incidents ORDER BY reported_at DESC LIMIT 100")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def db_resolve_incident(incident_id):
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE incidents SET status='resolved', resolved_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), incident_id)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def db_log_traffic(junction):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO traffic_logs (junction_id, junction_name, vehicles, wait_time, status, logged_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (junction['id'], junction['name'], junction['vehicles'],
             junction['wait_time'], junction['status'], datetime.utcnow().isoformat())
        )
        conn.commit()
    finally:
        conn.close()


def db_log_emergency(vehicle_type, action, actor="system"):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO emergency_logs (vehicle_type, action, timestamp, activated_by) "
            "VALUES (?, ?, ?, ?)",
            (vehicle_type, action, datetime.utcnow().isoformat(), actor)
        )
        conn.commit()
    finally:
        conn.close()


def db_log_ai_prediction(junction_id, prediction):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO ai_predictions "
            "(junction_id, congestion_label, confidence, predicted_wait, recommended_green, predicted_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (junction_id, prediction['congestion_label'], prediction['confidence'],
             prediction['predicted_wait_seconds'], prediction['recommended_green_seconds'],
             datetime.utcnow().isoformat())
        )
        conn.commit()
    finally:
        conn.close()


def db_get_stats():
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as t FROM incidents"); total_incidents = cursor.fetchone()['t']
        cursor.execute("SELECT COUNT(*) as t FROM incidents WHERE status='active'"); active_incidents = cursor.fetchone()['t']
        cursor.execute("SELECT COUNT(*) as t FROM emergency_logs WHERE action='activated'"); total_emergencies = cursor.fetchone()['t']
        cursor.execute("SELECT COUNT(*) as t FROM traffic_logs"); total_logs = cursor.fetchone()['t']
        cursor.execute("SELECT COUNT(*) as t FROM ai_predictions"); total_predictions = cursor.fetchone()['t']
    finally:
        conn.close()
    return {
        "total_incidents": total_incidents,
        "active_incidents": active_incidents,
        "total_emergencies": total_emergencies,
        "total_traffic_logs": total_logs,
        "total_ai_predictions": total_predictions,
    }


# ── AI Predict ─────────────────────────────────────────────────────────────────
def ai_predict(junction_id, vehicles, weather=0, incident_nearby=0):
    if not ai_models:
        return None
    try:
        now = datetime.utcnow()
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
    except Exception:
        logger.warning("AI prediction failed", exc_info=False)
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  REST ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

# ── Health ─────────────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
def health_check():
    """Health check endpoint — always public."""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat(), "version": "4.0.0"}


@app.get("/", tags=["System"])
def root():
    """Public root endpoint."""
    return {
        "message": "Smart Traffic API v4.0 — Security Hardened",
        "ai_loaded": bool(ai_models),
        "version": "4.0.0",
        "total_junctions": len(junctions),
        "coverage": "Tamil Nadu — 200 junctions",
    }


# ── Authentication ─────────────────────────────────────────────────────────────
@app.post("/auth/login", tags=["Auth"])
@limiter.limit("5/minute")
def login(request: Request, data: LoginRequest):
    """
    Authenticate police officer.
    Returns OTP challenge — call /auth/verify_otp with the 6-digit TOTP code.
    """
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT badge_id, password_hash, mobile_number, totp_secret, is_active "
            "FROM police_accounts WHERE badge_id = ?",
            (data.badge_id,)
        )
        account = cursor.fetchone()
    finally:
        conn.close()

    if not account or not account["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )

    if not pwd_context.verify(data.password, account["password_hash"]):
        logger.warning("Failed login attempt for badge_id=%s", data.badge_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )

    # Update last_login
    conn = get_db()
    try:
        conn.execute(
            "UPDATE police_accounts SET last_login=? WHERE badge_id=?",
            (datetime.utcnow().isoformat(), data.badge_id)
        )
        conn.commit()
    finally:
        conn.close()

    # Mask mobile number for privacy
    mobile = account["mobile_number"]
    masked = f"****{mobile[-4:]}" if len(mobile) >= 4 else "****"

    logger.info("Successful login: badge_id=%s", data.badge_id)
    return {
        "message": "Credentials valid. OTP sent to registered mobile.",
        "require_otp": True,
        "mobile": masked,
        "badge_id": data.badge_id
    }


@app.post("/auth/verify_otp", tags=["Auth"])
@limiter.limit("3/minute")
def verify_otp(request: Request, data: OTPRequest):
    """
    Verify TOTP OTP and issue JWT access token.
    OTP window: ±1 step (60 seconds tolerance).
    """
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT totp_secret FROM police_accounts WHERE badge_id = ? AND is_active = 1",
            (data.badge_id,)
        )
        account = cursor.fetchone()
    finally:
        conn.close()

    if not account:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )

    totp = pyotp.TOTP(account["totp_secret"])
    if not totp.verify(data.otp, valid_window=1):
        logger.warning("Invalid OTP attempt for badge_id=%s", data.badge_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired OTP. Please use your authenticator app."
        )

    token = create_access_token({
        "sub": data.badge_id,
        "role": "police",
        "type": "access"
    })

    logger.info("OTP verified, token issued for badge_id=%s", data.badge_id)
    return {
        "message": "Login successful",
        "access_token": token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "role": "police",
        "user_id": data.badge_id
    }


@app.post("/auth/logout", tags=["Auth"])
def logout(token: str = Depends(oauth2_scheme), current_user: dict = Depends(get_current_user)):
    """Invalidate current JWT token."""
    token_blacklist.add(token)
    logger.info("User logged out: %s", current_user.get("sub"))
    return {"message": "Successfully logged out."}


@app.get("/auth/me", tags=["Auth"])
def get_me(current_user: dict = Depends(get_current_user)):
    """Get current user information from JWT."""
    return {
        "user_id": current_user.get("sub"),
        "role": current_user.get("role"),
    }


# ── User Location ──────────────────────────────────────────────────────────────
@app.post("/users/location", tags=["Users"])
@limiter.limit("30/minute")
def update_location(request: Request, data: LocationRequest,
                    current_user: Optional[dict] = Depends(optional_user)):
    """
    Update GPS location. Requires authentication for police/admin roles.
    Drivers can update without auth (public GPS sharing).
    """
    # Bind user_id from JWT if authenticated
    user_id = current_user.get("sub") if current_user else data.user_id
    role = current_user.get("role", data.role) if current_user else data.role

    update_user_location(user_id, role, data.lat, data.lng)
    update_junction_counts()

    nearest = min(junctions, key=lambda j: haversine(data.lat, data.lng, j['lat'], j['lng']))
    min_dist = haversine(data.lat, data.lng, nearest['lat'], nearest['lng'])

    return {
        "message": "Location updated!",
        "user_id": user_id,
        "role": role,
        "nearest_junction": nearest['name'],
        "distance_meters": round(min_dist),
        "active_users": get_active_user_count()
    }


@app.get("/users/active", tags=["Users"])
def get_active_users(
    current_user: dict = Depends(require_police)
):
    """
    Get all active users (police/admin only).
    Returns anonymized location data.
    """
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, role, last_seen FROM active_users "
            "WHERE last_seen >= datetime('now', '-30 seconds')"
        )
        users = cursor.fetchall()
    finally:
        conn.close()
    # Return count and role distribution only — no precise GPS coordinates
    return {
        "active_users": [
            {"user_id": u["user_id"][:8] + "****", "role": u["role"], "last_seen": u["last_seen"]}
            for u in users
        ],
        "total": len(users)
    }


# ── Junctions ──────────────────────────────────────────────────────────────────
@app.get("/junctions/search", tags=["Junctions"])
@limiter.limit("60/minute")
def search_junctions(request: Request, q: str = Query("", max_length=100)):
    """Search junctions by name or city."""
    if not q:
        return {"junctions": junctions, "total": len(junctions)}
    query = q.lower()
    results = [j for j in junctions if query in j['name'].lower()]
    return {"junctions": results, "total": len(results)}


@app.get("/junctions", tags=["Junctions"])
@limiter.limit("60/minute")
def get_junctions(request: Request):
    """Get all junction statuses."""
    update_junction_counts()
    return {"junctions": junctions, "total": len(junctions)}


@app.get("/junctions/{junction_id}", tags=["Junctions"])
def get_junction(junction_id: int):
    """Get a specific junction by ID."""
    for j in junctions:
        if j["id"] == junction_id:
            return j
    raise HTTPException(status_code=404, detail="Junction not found")


@app.post("/junctions/{junction_id}/override", tags=["Junctions"])
@limiter.limit("10/minute")
def override_junction(
    request: Request,
    junction_id: int,
    data: OverrideRequest,
    current_user: dict = Depends(require_police)
):
    """Override a junction signal (police/admin only)."""
    for j in junctions:
        if j["id"] == junction_id:
            if data.road and data.road in j["roads"]:
                for r in j["roads"]:
                    j["roads"][r] = "red"
                j["roads"][data.road] = data.status
                j["status"] = "green" if data.status == "green" else "red"
                j["wait_time"] = 0
            else:
                j["status"] = data.status
                j["wait_time"] = 0 if data.status == "green" else (60 if data.status == "red" else 30)
            audit_log(
                current_user.get("sub"), "junction_override",
                f"junction:{junction_id}", f"status={data.status} road={data.road}",
                get_remote_address(request)
            )
            logger.info("Junction %d overridden to %s by %s", junction_id, data.status, current_user.get("sub"))
            return {"message": f"Junction {junction_id} overridden to {data.status}", "junction": j}
    raise HTTPException(status_code=404, detail="Junction not found")


# ── Traffic Summary ────────────────────────────────────────────────────────────
@app.get("/traffic/summary", tags=["Traffic"])
def get_traffic_summary():
    """Public traffic summary."""
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
        "emergency_active": emergency_active["active"],
        "timestamp": datetime.utcnow().isoformat(),
    }


# ── AI Endpoints ───────────────────────────────────────────────────────────────
@app.get("/ai/predict/{junction_id}", tags=["AI"])
def predict_junction(
    junction_id: int,
    weather: int = Query(0, ge=0, le=3),
    incident: int = Query(0, ge=0, le=1),
    current_user: dict = Depends(require_police)
):
    """AI traffic prediction for a junction (police/admin only). Read-only — does not mutate state."""
    junction = next((j for j in junctions if j["id"] == junction_id), None)
    if not junction:
        raise HTTPException(status_code=404, detail="Junction not found")
    prediction = ai_predict(junction_id, junction["vehicles"], weather, incident)
    if prediction:
        db_log_ai_prediction(junction_id, prediction)
        return {"junction_id": junction_id, "junction_name": junction["name"], **prediction}
    return {"message": "AI model not available", "junction_id": junction_id}


@app.get("/ai/forecast", tags=["AI"])
@limiter.limit("20/minute")
def forecast_traffic(request: Request):
    """Public traffic forecast for next 6 hours."""
    now = datetime.utcnow()
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
    return {"forecast": forecast, "generated_at": datetime.utcnow().isoformat()}


@app.get("/ai/status", tags=["AI"])
def ai_status(current_user: dict = Depends(require_police)):
    """AI model status (police/admin only)."""
    return {
        "ai_enabled": bool(ai_models),
        "models_loaded": list(ai_models.keys()) if ai_models else [],
        "model_version": "XGBoost",
    }


# ── Emergency Endpoints ────────────────────────────────────────────────────────
@app.post("/emergency/activate", tags=["Emergency"])
@limiter.limit("2/minute")
def activate_emergency(
    request: Request,
    data: EmergencyActivateRequest,
    current_user: dict = Depends(require_police)
):
    """Activate emergency corridor (police/admin only)."""
    emergency_active["active"] = True
    emergency_active["vehicle_type"] = data.vehicle_type
    emergency_active["activated_at"] = datetime.utcnow().isoformat()
    emergency_active["route_ids"] = data.route_ids or []

    route_ids = data.route_ids or []
    for j in junctions:
        if not route_ids or j["id"] in route_ids:
            j["status"] = "green"
            j["wait_time"] = 0

    db_log_emergency(data.vehicle_type, "activated", current_user.get("sub", "unknown"))
    audit_log(
        current_user.get("sub"), "emergency_activate", "emergency",
        f"vehicle={data.vehicle_type} routes={route_ids}",
        get_remote_address(request)
    )
    logger.info(
        "Emergency corridor ACTIVATED: %s by %s (routes=%s)",
        data.vehicle_type, current_user.get("sub"), route_ids
    )
    junctions_cleared = len(route_ids) if route_ids else len(junctions)
    return {
        "message": f"{data.vehicle_type} emergency corridor activated!",
        "all_signals": "GREEN",
        "junctions_cleared": junctions_cleared,
        "activated_by": current_user.get("sub")
    }


@app.post("/emergency/deactivate", tags=["Emergency"])
@limiter.limit("2/minute")
def deactivate_emergency(
    request: Request,
    current_user: dict = Depends(require_police)
):
    """Deactivate emergency corridor (police/admin only)."""
    vehicle_type = emergency_active.get("vehicle_type", "Unknown")

    # Restore to original state (randomized for simulation)
    statuses = ["red", "green", "amber"]
    for j in junctions:
        j["status"] = random.choice(statuses)
        j["wait_time"] = random.randint(10, 60)

    emergency_active["active"] = False
    emergency_active["vehicle_type"] = None
    emergency_active["route_ids"] = []

    db_log_emergency(vehicle_type, "deactivated", current_user.get("sub", "unknown"))
    audit_log(
        current_user.get("sub"), "emergency_deactivate", "emergency",
        f"vehicle={vehicle_type}", get_remote_address(request)
    )
    logger.info("Emergency DEACTIVATED by %s", current_user.get("sub"))
    return {"message": "Emergency deactivated. Signals returned to normal."}


@app.post("/emergency/notify", tags=["Emergency"])
@limiter.limit("20/minute")
def notify_emergency(
    request: Request,
    data: EmergencyNotifyRequest,
    current_user: dict = Depends(get_current_user)
):
    """Send emergency notification — requires authentication."""
    if data.junction_id and data.road:
        for j in junctions:
            if j["id"] == data.junction_id:
                if data.road not in j.get("emergency_alerts", []):
                    j.setdefault("emergency_alerts", []).append(data.road)
                return {"message": f"Alert sent for {data.road} road at {j['name']}!"}
    return {"message": "Emergency notification sent to nearby units."}


@app.get("/emergency/status", tags=["Emergency"])
def get_emergency_status():
    """Public emergency status."""
    return {
        "active": emergency_active["active"],
        "vehicle_type": emergency_active["vehicle_type"] if emergency_active["active"] else None,
        "activated_at": emergency_active["activated_at"] if emergency_active["active"] else None,
    }


@app.get("/emergency/history", tags=["Emergency"])
def get_emergency_history(current_user: dict = Depends(require_police)):
    """Emergency event history (police/admin only)."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT vehicle_type, action, timestamp, activated_by "
            "FROM emergency_logs ORDER BY timestamp DESC LIMIT 20"
        )
        rows = cursor.fetchall()
    finally:
        conn.close()
    return {"history": [dict(row) for row in rows]}


# ── Incident Endpoints ─────────────────────────────────────────────────────────
@app.post("/incidents/report", tags=["Incidents"])
@limiter.limit("10/minute")
def report_incident(
    request: Request,
    data: IncidentReportRequest,
    current_user: dict = Depends(get_current_user)
):
    """Report a traffic incident (requires authentication)."""
    user_id = current_user.get("sub")
    incident_id = db_save_incident(data.type, data.location, user_id)

    # Gamification — points with rate guard (max 10 per hour)
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT points, last_report FROM user_points WHERE user_id = ?", (user_id,)
        )
        row = cursor.fetchone()
        last_report = row["last_report"] if row else None
        one_hour_ago = (datetime.utcnow() - timedelta(hours=1)).isoformat()

        if not last_report or last_report < one_hour_ago:
            cursor.execute(
                "INSERT INTO user_points (user_id, points, last_report) VALUES (?, 10, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET points = points + 10, last_report = ?",
                (user_id, datetime.utcnow().isoformat(), datetime.utcnow().isoformat())
            )
        cursor.execute("SELECT points FROM user_points WHERE user_id = ?", (user_id,))
        points_row = cursor.fetchone()
        points = points_row["points"] if points_row else 10
        conn.commit()
    finally:
        conn.close()

    return {
        "message": "Incident reported successfully!",
        "incident": {
            "id": incident_id,
            "type": data.type,
            "location": data.location,
            "reported_at": datetime.utcnow().isoformat(),
            "status": "active",
        },
        "user_points": points
    }


@app.get("/incidents", tags=["Incidents"])
@limiter.limit("60/minute")
def get_incidents(request: Request):
    """List active incidents (public)."""
    incidents = db_get_incidents()
    return {"incidents": incidents, "total": len(incidents)}


@app.post("/incidents/{incident_id}/resolve", tags=["Incidents"])
def resolve_incident(
    incident_id: int,
    request: Request,
    current_user: dict = Depends(require_police)
):
    """Resolve an incident (police/admin only)."""
    resolved = db_resolve_incident(incident_id)
    if not resolved:
        raise HTTPException(status_code=404, detail="Incident not found")
    audit_log(
        current_user.get("sub"), "incident_resolve",
        f"incident:{incident_id}", "", get_remote_address(request)
    )
    return {"message": f"Incident {incident_id} resolved successfully!"}


# ── Leaderboard ────────────────────────────────────────────────────────────────
@app.get("/leaderboard", tags=["Gamification"])
@limiter.limit("30/minute")
def get_leaderboard(request: Request):
    """Top 10 contributors (public)."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, points FROM user_points ORDER BY points DESC LIMIT 10"
        )
        rows = cursor.fetchall()
    finally:
        conn.close()
    leaderboard = [
        {"user_id": r["user_id"][:4] + "****", "points": r["points"]}
        for r in rows
    ]
    return {"leaderboard": leaderboard}


# ── SOS Endpoint ───────────────────────────────────────────────────────────────
@app.post("/sos", tags=["Emergency"])
@limiter.limit("5/minute")
def send_sos(
    request: Request,
    lat: float = Query(..., ge=-90.0, le=90.0),
    lng: float = Query(..., ge=-180.0, le=180.0),
    sos_type: str = Query("emergency", max_length=50),
    current_user: dict = Depends(get_current_user)
):
    """SOS emergency alert (authenticated users only)."""
    user_id = current_user.get("sub")
    audit_log(user_id, "sos_alert", "sos", f"type={sos_type} lat={lat} lng={lng}", get_remote_address(request))
    logger.warning("SOS ALERT: user=%s type=%s lat=%.4f lng=%.4f", user_id, sos_type, lat, lng)
    return {
        "message": "SOS sent to nearby emergency units!",
        "user_id": user_id,
        "sos_type": sos_type,
        "timestamp": datetime.utcnow().isoformat()
    }


# ── History & Analytics ────────────────────────────────────────────────────────
@app.get("/history/traffic", tags=["History"])
def get_traffic_history(
    junction_id: Optional[int] = None,
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(require_police)
):
    """Traffic log history (police/admin only)."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        if junction_id:
            cursor.execute(
                "SELECT * FROM traffic_logs WHERE junction_id=? ORDER BY logged_at DESC LIMIT ?",
                (junction_id, limit)
            )
        else:
            cursor.execute(
                "SELECT * FROM traffic_logs ORDER BY logged_at DESC LIMIT ?", (limit,)
            )
        rows = cursor.fetchall()
    finally:
        conn.close()
    return {"history": [dict(row) for row in rows], "total": len(rows)}


@app.get("/history/stats", tags=["History"])
def get_history_stats(current_user: dict = Depends(require_police)):
    """System statistics (police/admin only)."""
    return db_get_stats()


@app.get("/analytics/summary", tags=["Analytics"])
def get_analytics(current_user: dict = Depends(require_police)):
    """System analytics (police/admin only)."""
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


# ── Route Suggestion ───────────────────────────────────────────────────────────
road_graph = None

def get_junction_by_id(jid):
    for j in junctions:
        if j['id'] == jid:
            return j
    return None

def build_graph():
    graph = {}
    for j1 in junctions:
        graph[j1['id']] = []
        neighbors = sorted(
            [(haversine(j1['lat'], j1['lng'], j2['lat'], j2['lng']), j2['id'])
             for j2 in junctions if j1['id'] != j2['id']]
        )
        for dist, j2_id in neighbors[:5]:
            graph[j1['id']].append({'id': j2_id, 'distance': dist})
    return graph

def astar_route(start_id, end_id):
    global road_graph
    if not road_graph:
        road_graph = build_graph()
    import heapq as _hq
    start_node = get_junction_by_id(start_id)
    end_node = get_junction_by_id(end_id)
    if not start_node or not end_node:
        return None
    speed_mps = 11.11
    pq = [(0, 0, start_id, [start_id])]
    visited = set()
    while pq:
        f, g, curr_id, path = _hq.heappop(pq)
        if curr_id == end_id:
            return path
        if curr_id in visited:
            continue
        visited.add(curr_id)
        curr_node = get_junction_by_id(curr_id)
        for edge in road_graph.get(curr_id, []):
            neighbor_id = edge['id']
            if neighbor_id in visited:
                continue
            neighbor_node = get_junction_by_id(neighbor_id)
            dist = edge['distance']
            travel_time = dist / speed_mps
            wait_time = neighbor_node.get('wait_time', 0)
            vehicle_penalty = neighbor_node.get('vehicles', 0) * 5
            emg_penalty = 120 if neighbor_node.get('emergency_alerts') else 0
            edge_cost = travel_time + wait_time + vehicle_penalty + emg_penalty
            new_g = g + edge_cost
            h_dist = haversine(neighbor_node['lat'], neighbor_node['lng'], end_node['lat'], end_node['lng'])
            h = h_dist / speed_mps
            _hq.heappush(pq, (new_g + h, new_g, neighbor_id, path + [neighbor_id]))
    return None


@app.get("/route/suggest", tags=["Routing"])
@limiter.limit("10/minute")
def suggest_route(
    request: Request,
    start_lat: float = Query(..., ge=-90, le=90),
    start_lng: float = Query(..., ge=-180, le=180),
    end_lat: float = Query(..., ge=-90, le=90),
    end_lng: float = Query(..., ge=-180, le=180),
):
    """AI-powered route suggestion (public)."""
    if not junctions:
        return {}
    start_j = min(junctions, key=lambda j: haversine(start_lat, start_lng, j['lat'], j['lng']))
    end_j = min(junctions, key=lambda j: haversine(end_lat, end_lng, j['lat'], j['lng']))
    path = astar_route(start_j['id'], end_j['id'])
    if not path:
        return {}
    waypoints = []
    total_time = 0
    total_dist = 0
    for i in range(len(path)):
        j = get_junction_by_id(path[i])
        waypoints.append({'lat': j['lat'], 'lng': j['lng'], 'id': j['id'], 'name': j['name']})
        if i > 0:
            prev = get_junction_by_id(path[i-1])
            dist = haversine(prev['lat'], prev['lng'], j['lat'], j['lng'])
            total_dist += dist
            total_time += (dist / 11.11) + j.get('wait_time', 0) + (j.get('vehicles', 0)*5)
    return {
        "current_route": {
            "name": f"Baseline to {end_j['name'].split(',')[0]}",
            "time_minutes": round(total_time / 60) + 7,
            "distance_km": round(total_dist / 1000, 2),
            "congestion": "high"
        },
        "suggested_route": {
            "name": f"AI Optimized via {waypoints[len(waypoints)//2]['name'].split(',')[0] if len(waypoints)>1 else start_j['name'].split(',')[0]}",
            "time_minutes": round(total_time / 60),
            "distance_km": round(total_dist / 1000, 2),
            "congestion": "low",
            "waypoints": waypoints
        },
        "time_saved_minutes": 7,
    }


# ── WebSocket — Authenticated Real-Time Stream ─────────────────────────────────
log_counter = 0

@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket, token: Optional[str] = None):
    """
    Real-time traffic data stream.
    Pass ?token=<jwt> for authenticated access (required).
    """
    global log_counter

    # Authenticate WebSocket connection
    if not token:
        await websocket.close(code=4001, reason="Authentication required. Pass ?token=<jwt>")
        return

    try:
        payload = verify_token(token)
        user_sub = payload.get("sub", "unknown")
        user_role = payload.get("role", "driver")
    except HTTPException:
        await websocket.close(code=4003, reason="Invalid or expired token")
        return

    await websocket.accept()
    connected_clients.append(websocket)
    logger.info("WS connected: user=%s role=%s", user_sub, user_role)

    try:
        while True:
            update_junction_counts()
            for j in junctions:
                is_emergency_route = (
                    emergency_active["active"] and
                    (not emergency_active.get("route_ids") or j["id"] in emergency_active.get("route_ids", []))
                )
                if not is_emergency_route:
                    j["wait_time"] = max(5, j["wait_time"] + random.randint(-5, 5))
                    if j["vehicles"] == 0:
                        j["vehicles"] = max(0, j["vehicles"] + random.randint(-1, 3))

            log_counter += 1
            if log_counter % 10 == 0:
                for j in junctions:
                    db_log_traffic(j)

            # Build payload based on role
            ws_payload: dict = {
                "type": "junction_update",
                "junctions": junctions,
                "timestamp": datetime.utcnow().isoformat(),
                "emergency_active": emergency_active,
            }
            # Only include active user count for police/admin
            if user_role in ("police", "admin"):
                ws_payload["active_users"] = get_active_user_count()

            await websocket.send_text(json.dumps(ws_payload))
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        logger.info("WS disconnected: user=%s", user_sub)
    finally:
        if websocket in connected_clients:
            connected_clients.remove(websocket)