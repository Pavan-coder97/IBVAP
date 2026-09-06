"""
ibvap/core/database.py
SQLite database layer for IBVAP.

Tables:
  alerts     — Security event log (intrusion, suspect, night, loitering, crowd)
  profiles   — FRS face embeddings with role (authorized / suspect)
  stats      — Counters for dashboard stat cards
  cameras    — Camera registry (cam_id, name, source_url, location)
  plate_log  — ANPR plate read history
"""

import sqlite3
import os
import json

DB_PATH = 'ibvap.db'


def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")   # Better concurrent read/write
    return conn


def init_db():
    conn = get_connection()
    c = conn.cursor()

    # ── Alerts ────────────────────────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS alerts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT,
            alert_type  TEXT,
            message     TEXT,
            image_path  TEXT,
            cam_id      TEXT DEFAULT 'CAM-01',
            location    TEXT DEFAULT ''
        )
    ''')
    # Add columns if upgrading from old schema
    for col, coldef in [('cam_id', "TEXT DEFAULT 'CAM-01'"),
                        ('location', "TEXT DEFAULT ''")]:
        try:
            c.execute(f"ALTER TABLE alerts ADD COLUMN {col} {coldef}")
        except Exception:
            pass

    # ── Profiles (FRS) ────────────────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS profiles (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            name     TEXT UNIQUE,
            features TEXT,
            role     TEXT DEFAULT 'authorized'
        )
    ''')
    try:
        c.execute("ALTER TABLE profiles ADD COLUMN role TEXT DEFAULT 'authorized'")
    except Exception:
        pass

    # ── Stats ─────────────────────────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS stats (
            key   TEXT PRIMARY KEY,
            value INTEGER
        )
    ''')
    default_stats = [
        'total_vehicles', 'helmet_violations', 'plates_read',
        'persons_detected', 'intrusion_alerts', 'night_alerts',
        'suspicious_count', 'suspect_alerts', 'crowd_alerts',
    ]
    for key in default_stats:
        c.execute("INSERT OR IGNORE INTO stats (key, value) VALUES (?, 0)", (key,))

    # ── Cameras ───────────────────────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS cameras (
            cam_id      TEXT PRIMARY KEY,
            name        TEXT,
            source_url  TEXT,
            location    TEXT DEFAULT '',
            active      INTEGER DEFAULT 1
        )
    ''')

    # ── Plate Log ─────────────────────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS plate_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT,
            plate_text  TEXT,
            confidence  REAL,
            cam_id      TEXT DEFAULT 'CAM-01',
            image_path  TEXT DEFAULT ''
        )
    ''')

    conn.commit()
    conn.close()


# ── Alert functions ────────────────────────────────────────────────────────

def log_alert(alert_type: str, message: str, image_path: str = "",
              cam_id: str = "CAM-01", location: str = ""):
    import datetime
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO alerts (timestamp, alert_type, message, image_path, cam_id, location) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (datetime.datetime.now().isoformat(), alert_type, message,
         image_path, cam_id, location)
    )
    conn.commit()
    conn.close()


def get_alerts(limit: int = 50, alert_type: str = None,
               cam_id: str = None, date_from: str = None, date_to: str = None):
    conn = get_connection()
    c = conn.cursor()
    query = ("SELECT id, timestamp, alert_type, message, image_path, cam_id, location "
             "FROM alerts WHERE 1=1")
    params = []
    if alert_type:
        query += " AND alert_type = ?"
        params.append(alert_type)
    if cam_id:
        query += " AND cam_id = ?"
        params.append(cam_id)
    if date_from:
        query += " AND timestamp >= ?"
        params.append(date_from)
    if date_to:
        query += " AND timestamp <= ?"
        params.append(date_to)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    return [
        {"id": r[0], "timestamp": r[1], "alert_type": r[2],
         "message": r[3], "image_path": r[4], "cam_id": r[5], "location": r[6]}
        for r in rows
    ]


def get_all_alert_types():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT DISTINCT alert_type FROM alerts ORDER BY alert_type")
    types = [r[0] for r in c.fetchall()]
    conn.close()
    return types


# ── Profile (FRS) functions ────────────────────────────────────────────────

def add_profile(name: str, features, role: str = 'authorized'):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "REPLACE INTO profiles (name, features, role) VALUES (?, ?, ?)",
        (name, json.dumps(features.tolist()), role)
    )
    conn.commit()
    conn.close()


def delete_profile(name: str):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM profiles WHERE name = ?", (name,))
    conn.commit()
    conn.close()


def load_profiles():
    """Returns dict: {name: (feature_array, role)}"""
    import numpy as np
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT name, features, role FROM profiles")
    rows = c.fetchall()
    conn.close()
    profiles = {}
    for r in rows:
        profiles[r[0]] = (np.array(json.loads(r[1]), dtype=np.float32), r[2])
    return profiles


def list_profiles():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT name, role FROM profiles ORDER BY name")
    rows = c.fetchall()
    conn.close()
    return [{"name": r[0], "role": r[1]} for r in rows]


# ── Stats functions ────────────────────────────────────────────────────────

def increment_stat(key: str):
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO stats (key, value) VALUES (?, 0)", (key,))
    c.execute("UPDATE stats SET value = value + 1 WHERE key = ?", (key,))
    conn.commit()
    conn.close()


def get_stats():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT key, value FROM stats")
    rows = c.fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


# ── Camera registry functions ──────────────────────────────────────────────

def add_camera(cam_id: str, name: str, source_url: str, location: str = ""):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "REPLACE INTO cameras (cam_id, name, source_url, location, active) VALUES (?,?,?,?,1)",
        (cam_id, name, source_url, location)
    )
    conn.commit()
    conn.close()


def remove_camera(cam_id: str):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM cameras WHERE cam_id = ?", (cam_id,))
    conn.commit()
    conn.close()


def list_cameras_db():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT cam_id, name, source_url, location, active FROM cameras")
    rows = c.fetchall()
    conn.close()
    return [
        {"cam_id": r[0], "name": r[1], "source_url": r[2],
         "location": r[3], "active": bool(r[4])}
        for r in rows
    ]


# ── Plate log functions ────────────────────────────────────────────────────

def log_plate(plate_text: str, confidence: float, cam_id: str = "CAM-01",
              image_path: str = ""):
    import datetime
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO plate_log (timestamp, plate_text, confidence, cam_id, image_path) "
        "VALUES (?, ?, ?, ?, ?)",
        (datetime.datetime.now().isoformat(), plate_text, confidence, cam_id, image_path)
    )
    conn.commit()
    conn.close()


def get_plate_log(limit: int = 100, cam_id: str = None):
    conn = get_connection()
    c = conn.cursor()
    if cam_id:
        c.execute(
            "SELECT id, timestamp, plate_text, confidence, cam_id, image_path "
            "FROM plate_log WHERE cam_id=? ORDER BY id DESC LIMIT ?",
            (cam_id, limit)
        )
    else:
        c.execute(
            "SELECT id, timestamp, plate_text, confidence, cam_id, image_path "
            "FROM plate_log ORDER BY id DESC LIMIT ?",
            (limit,)
        )
    rows = c.fetchall()
    conn.close()
    return [
        {"id": r[0], "timestamp": r[1], "plate_text": r[2],
         "confidence": r[3], "cam_id": r[4], "image_path": r[5]}
        for r in rows
    ]


def search_plate(query: str, limit: int = 50):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT id, timestamp, plate_text, confidence, cam_id, image_path "
        "FROM plate_log WHERE plate_text LIKE ? ORDER BY id DESC LIMIT ?",
        (f"%{query.upper()}%", limit)
    )
    rows = c.fetchall()
    conn.close()
    return [
        {"id": r[0], "timestamp": r[1], "plate_text": r[2],
         "confidence": r[3], "cam_id": r[4], "image_path": r[5]}
        for r in rows
    ]


# ── Analytics functions ────────────────────────────────────────────────────

def get_analytics_timeline(hours: int = 24):
    """Returns hourly alert counts for the last N hours."""
    import datetime
    conn = get_connection()
    c = conn.cursor()
    since = (datetime.datetime.now() -
             datetime.timedelta(hours=hours)).isoformat()
    c.execute(
        "SELECT strftime('%H', timestamp) as hr, alert_type, COUNT(*) as cnt "
        "FROM alerts WHERE timestamp >= ? "
        "GROUP BY hr, alert_type ORDER BY hr",
        (since,)
    )
    rows = c.fetchall()
    conn.close()
    return [{"hour": r[0], "alert_type": r[1], "count": r[2]} for r in rows]


def get_analytics_summary():
    """Returns aggregated statistics for the analytics dashboard."""
    conn = get_connection()
    c = conn.cursor()

    # Alert type breakdown
    c.execute("SELECT alert_type, COUNT(*) FROM alerts GROUP BY alert_type")
    alert_breakdown = {r[0]: r[1] for r in c.fetchall()}

    # Vehicle class breakdown from alerts (embedded in message)
    c.execute("SELECT COUNT(*) FROM plate_log")
    total_plates = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM profiles WHERE role='authorized'")
    authorized_count = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM profiles WHERE role='suspect'")
    suspect_count = c.fetchone()[0]

    conn.close()

    stats = get_stats()
    return {
        "stats": stats,
        "alert_breakdown": alert_breakdown,
        "total_plates": total_plates,
        "authorized_profiles": authorized_count,
        "suspect_profiles": suspect_count,
    }


# Initialize on import
init_db()
