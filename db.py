# db.py — SQLite helpers for the F&O (Karishma) dashboard.
import sqlite3
import threading
from fno_config import DB_PATH

_lock = threading.Lock()


def _open():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def db_read(sql, params=()):
    with _lock:
        conn = _open()
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()


def db_read_one(sql, params=()):
    with _lock:
        conn = _open()
        try:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def db_write(sql, params=()):
    with _lock:
        conn = _open()
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()


def init_db():
    with _lock:
        conn = _open()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS fno_invoices(
                    voucher      TEXT PRIMARY KEY,
                    invoice_num  TEXT,
                    date         TEXT,
                    customer     TEXT,
                    total        REAL,
                    status       TEXT DEFAULT 'pending',   -- pending | posted | failed
                    irn          TEXT,
                    qr_code      TEXT,
                    api_response TEXT,
                    posted_at    TEXT
                );
            """)
            conn.commit()
        finally:
            conn.close()


init_db()
