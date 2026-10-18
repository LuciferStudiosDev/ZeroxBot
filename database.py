import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.getenv("DATABASE_PATH", "zerox_ptero.sqlite3")

@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS linked_accounts (
            discord_user_id TEXT PRIMARY KEY,
            ptero_user_id INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS whitelist (
            discord_user_id TEXT PRIMARY KEY,
            note TEXT DEFAULT '',
            added_by TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS server_records (
            identifier TEXT PRIMARY KEY,
            owner_discord_id TEXT,
            plan TEXT DEFAULT 'unknown',
            expires_at TEXT,
            suspended INTEGER DEFAULT 0,
            autobackup INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identifier TEXT NOT NULL,
            action TEXT NOT NULL,
            schedule_text TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            created_by TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS billing_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_user_id TEXT NOT NULL,
            identifier TEXT NOT NULL,
            amount TEXT NOT NULL,
            status TEXT DEFAULT 'unpaid',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)

def link_user(discord_id: int, ptero_user_id: int):
    with connect() as db:
        db.execute("INSERT INTO linked_accounts(discord_user_id,ptero_user_id) VALUES(?,?) "
                   "ON CONFLICT(discord_user_id) DO UPDATE SET ptero_user_id=excluded.ptero_user_id",
                   (str(discord_id), int(ptero_user_id)))

def unlink_user(discord_id: int):
    with connect() as db:
        db.execute("DELETE FROM linked_accounts WHERE discord_user_id=?", (str(discord_id),))

def get_link(discord_id: int):
    with connect() as db:
        row = db.execute("SELECT * FROM linked_accounts WHERE discord_user_id=?", (str(discord_id),)).fetchone()
        return dict(row) if row else None

def whitelist_add(discord_id: int, note: str, added_by: int):
    with connect() as db:
        db.execute("INSERT INTO whitelist(discord_user_id,note,added_by) VALUES(?,?,?) "
                   "ON CONFLICT(discord_user_id) DO UPDATE SET note=excluded.note, added_by=excluded.added_by",
                   (str(discord_id), note[:300], str(added_by)))

def whitelist_remove(discord_id: int):
    with connect() as db:
        db.execute("DELETE FROM whitelist WHERE discord_user_id=?", (str(discord_id),))

def whitelist_has(discord_id: int) -> bool:
    with connect() as db:
        return db.execute("SELECT 1 FROM whitelist WHERE discord_user_id=?", (str(discord_id),)).fetchone() is not None

def whitelist_list():
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM whitelist ORDER BY created_at DESC").fetchall()]

def save_server_record(identifier: str, owner_discord_id=None, plan="unknown", expires_at=None):
    with connect() as db:
        db.execute("INSERT INTO server_records(identifier,owner_discord_id,plan,expires_at) VALUES(?,?,?,?) "
                   "ON CONFLICT(identifier) DO UPDATE SET owner_discord_id=excluded.owner_discord_id, "
                   "plan=excluded.plan, expires_at=excluded.expires_at",
                   (identifier, str(owner_discord_id) if owner_discord_id else None, plan, expires_at))

def get_server_record(identifier: str):
    with connect() as db:
        row = db.execute("SELECT * FROM server_records WHERE identifier=?", (identifier,)).fetchone()
        return dict(row) if row else None

def set_server_flag(identifier: str, flag: str, value: int):
    allowed = {"suspended", "autobackup"}
    if flag not in allowed:
        raise ValueError("Invalid server flag")
    with connect() as db:
        db.execute(f"UPDATE server_records SET {flag}=? WHERE identifier=?", (int(value), identifier))

def add_schedule(identifier: str, action: str, schedule_text: str, created_by: int):
    with connect() as db:
        db.execute("INSERT INTO schedules(identifier,action,schedule_text,created_by) VALUES(?,?,?,?)",
                   (identifier, action, schedule_text[:120], str(created_by)))

def list_schedules(identifier=None):
    with connect() as db:
        if identifier:
            rows = db.execute("SELECT * FROM schedules WHERE identifier=? AND enabled=1 ORDER BY id DESC", (identifier,))
        else:
            rows = db.execute("SELECT * FROM schedules WHERE enabled=1 ORDER BY id DESC")
        return [dict(r) for r in rows.fetchall()]

def add_bill(discord_user_id: int, identifier: str, amount: str):
    with connect() as db:
        db.execute("INSERT INTO billing_records(discord_user_id,identifier,amount) VALUES(?,?,?)",
                   (str(discord_user_id), identifier, amount))

def list_bills(discord_user_id: int):
    with connect() as db:
        return [dict(r) for r in db.execute(
            "SELECT * FROM billing_records WHERE discord_user_id=? ORDER BY id DESC LIMIT 25",
            (str(discord_user_id),)).fetchall()]
