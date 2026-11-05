import sqlite3
import uuid
import secrets
import json
from datetime import datetime, timedelta
from typing import Optional
from config import DB_PATH, ORDER_PREFIX, PAYMENT_EXPIRY_MINS


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init():
    c = _conn()
    c.executescript("""
    PRAGMA journal_mode=WAL;

    CREATE TABLE IF NOT EXISTS users (
        user_id      INTEGER PRIMARY KEY,
        username     TEXT    DEFAULT '',
        full_name    TEXT    DEFAULT '',
        balance      INTEGER DEFAULT 0,
        total_spent  INTEGER DEFAULT 0,
        total_orders INTEGER DEFAULT 0,
        is_banned    INTEGER DEFAULT 0,
        created_at   TEXT    DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS orders (
        order_id     TEXT PRIMARY KEY,
        user_id      INTEGER REFERENCES users(user_id),
        service_id   TEXT,
        service_name TEXT,
        amount       INTEGER,
        extra_data   TEXT    DEFAULT '',
        status       TEXT    DEFAULT 'pending',
        note         TEXT    DEFAULT '',
        created_at   TEXT    DEFAULT CURRENT_TIMESTAMP,
        updated_at   TEXT    DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS transactions (
        tx_id        TEXT    PRIMARY KEY,
        user_id      INTEGER REFERENCES users(user_id),
        kind         TEXT,
        amount       INTEGER,
        ref_id       TEXT    DEFAULT '',
        description  TEXT    DEFAULT '',
        created_at   TEXT    DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS pending_payments (
        payment_id  TEXT    PRIMARY KEY,
        user_id     INTEGER REFERENCES users(user_id),
        amount      INTEGER,
        order_ref   TEXT    UNIQUE,
        order_data  TEXT    DEFAULT '{}',
        status      TEXT    DEFAULT 'pending',
        message_id  INTEGER DEFAULT 0,
        created_at  TEXT    DEFAULT CURRENT_TIMESTAMP,
        expires_at  TEXT
    );

    CREATE TABLE IF NOT EXISTS keys (
        key_id       TEXT    PRIMARY KEY,
        key_value    TEXT    UNIQUE,
        package_id   TEXT    DEFAULT '',
        package_name TEXT    DEFAULT '',
        user_id      INTEGER REFERENCES users(user_id),
        days         INTEGER DEFAULT 0,
        activated_at TEXT    DEFAULT CURRENT_TIMESTAMP,
        expires_at   TEXT    DEFAULT '',
        created_at   TEXT    DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS admins (
        user_id   INTEGER PRIMARY KEY,
        username  TEXT    DEFAULT '',
        added_at  TEXT    DEFAULT CURRENT_TIMESTAMP
    );
    """)
    c.commit()
    c.close()


def get_user(uid: int) -> Optional[dict]:
    c = _conn()
    row = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
    c.close()
    return dict(row) if row else None


def upsert_user(uid: int, username: str, full_name: str) -> dict:
    c = _conn()
    c.execute("""
        INSERT INTO users (user_id, username, full_name)
        VALUES (?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
            username  = excluded.username,
            full_name = excluded.full_name
    """, (uid, username or "", full_name or ""))
    c.commit()
    c.close()
    return get_user(uid)


def find_user_by_username_or_id(query: str) -> list[dict]:
    c = _conn()
    q = query.strip().lstrip("@")
    if q.isdigit():
        row = c.execute("SELECT * FROM users WHERE user_id=?", (int(q),)).fetchone()
        if row:
            rows = [dict(row)]
        else:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM users WHERE username LIKE ? LIMIT 10",
                (f"%{q}%",)
            ).fetchall()]
    else:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM users WHERE LOWER(username) LIKE LOWER(?) OR LOWER(full_name) LIKE LOWER(?) LIMIT 10",
            (f"%{q}%", f"%{q}%")
        ).fetchall()]
    c.close()
    return rows


def deduct(uid: int, amount: int, ref_id: str = "", desc: str = "") -> bool:
    c = _conn()
    try:
        row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
        if not row or row[0] < amount:
            c.close()
            return False
        c.execute(
            "UPDATE users SET balance=balance-?, total_spent=total_spent+? WHERE user_id=?",
            (amount, amount, uid)
        )
        _record_tx(c, uid, "debit", -amount, ref_id, desc)
        c.commit()
        c.close()
        return True
    except Exception:
        c.rollback()
        c.close()
        return False


def credit(uid: int, amount: int, ref_id: str = "", desc: str = ""):
    c = _conn()
    c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, uid))
    _record_tx(c, uid, "credit", amount, ref_id, desc)
    c.commit()
    c.close()


def _record_tx(c, uid, kind, amount, ref_id, desc):
    tx_id = uuid.uuid4().hex[:10].upper()
    c.execute(
        "INSERT INTO transactions (tx_id,user_id,kind,amount,ref_id,description) VALUES (?,?,?,?,?,?)",
        (tx_id, uid, kind, amount, ref_id, desc)
    )


def ban_user(uid: int, banned: int = 1):
    c = _conn()
    c.execute("UPDATE users SET is_banned=? WHERE user_id=?", (banned, uid))
    c.commit()
    c.close()


def all_users(limit: int = 100) -> list[dict]:
    c = _conn()
    rows = c.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def new_order(uid: int, service_id: str, service_name: str, amount: int, extra: str = "") -> str:
    oid = f"{ORDER_PREFIX}{datetime.now().strftime('%d%m%H%M')}{uuid.uuid4().hex[:4].upper()}"
    c = _conn()
    c.execute(
        "INSERT INTO orders (order_id,user_id,service_id,service_name,amount,extra_data) VALUES (?,?,?,?,?,?)",
        (oid, uid, service_id, service_name, amount, extra)
    )
    c.execute("UPDATE users SET total_orders=total_orders+1 WHERE user_id=?", (uid,))
    c.commit()
    c.close()
    return oid


def set_order_status(oid: str, status: str, note: str = ""):
    c = _conn()
    c.execute(
        "UPDATE orders SET status=?,note=?,updated_at=CURRENT_TIMESTAMP WHERE order_id=?",
        (status, note, oid)
    )
    c.commit()
    c.close()


def get_order(oid: str) -> Optional[dict]:
    c = _conn()
    row = c.execute("SELECT * FROM orders WHERE order_id=?", (oid,)).fetchone()
    c.close()
    return dict(row) if row else None


def get_orders(uid: int, limit: int = 10) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
        (uid, limit)
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_pending_orders(limit: int = 30) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM orders WHERE status IN ('pending','processing') ORDER BY created_at DESC LIMIT ?",
        (limit,)
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def new_payment(uid: int, amount: int, order_ref: str,
                order_data: dict, msg_id: int = 0) -> str:
    pid     = uuid.uuid4().hex[:8].upper()
    expires = (datetime.now() + timedelta(minutes=PAYMENT_EXPIRY_MINS)).isoformat()
    c = _conn()
    c.execute(
        "INSERT INTO pending_payments "
        "(payment_id,user_id,amount,order_ref,order_data,message_id,expires_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (pid, uid, amount, order_ref, json.dumps(order_data), msg_id, expires)
    )
    c.commit()
    c.close()
    return pid


def set_payment_msg_id(pid: str, msg_id: int):
    c = _conn()
    c.execute("UPDATE pending_payments SET message_id=? WHERE payment_id=?", (msg_id, pid))
    c.commit()
    c.close()


def get_payment_by_ref(ref: str) -> Optional[dict]:
    c = _conn()
    row = c.execute(
        "SELECT * FROM pending_payments WHERE order_ref=? AND status='pending'", (ref,)
    ).fetchone()
    c.close()
    return dict(row) if row else None


def get_payment_by_id(pid: str) -> Optional[dict]:
    c = _conn()
    row = c.execute(
        "SELECT * FROM pending_payments WHERE payment_id=?", (pid,)
    ).fetchone()
    c.close()
    return dict(row) if row else None


def complete_payment(pid: str) -> bool:
    c = _conn()
    cur = c.execute(
        "UPDATE pending_payments SET status='completed' "
        "WHERE payment_id=? AND status='pending'",
        (pid,)
    )
    changed = cur.rowcount > 0
    c.commit()
    c.close()
    return changed


def expire_payment(pid: str) -> bool:
    c = _conn()
    cur = c.execute(
        "UPDATE pending_payments SET status='expired' "
        "WHERE payment_id=? AND status='pending'",
        (pid,)
    )
    changed = cur.rowcount > 0
    c.commit()
    c.close()
    return changed


def get_user_pending_payments(uid: int, prefix: str = "") -> list[dict]:
    c = _conn()
    if prefix:
        rows = c.execute(
            "SELECT * FROM pending_payments "
            "WHERE user_id=? AND status='pending' AND order_ref LIKE ?",
            (uid, f"{prefix}%")
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM pending_payments "
            "WHERE user_id=? AND status='pending'",
            (uid,)
        ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_expired_payments() -> list[dict]:
    c = _conn()
    now  = datetime.now().isoformat()
    rows = c.execute(
        "SELECT * FROM pending_payments WHERE status='pending' AND expires_at < ?", (now,)
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def is_admin(uid: int) -> bool:
    c = _conn()
    r = c.execute("SELECT 1 FROM admins WHERE user_id=?", (uid,)).fetchone()
    c.close()
    return r is not None


def add_admin(uid: int, username: str = ""):
    c = _conn()
    c.execute(
        "INSERT OR IGNORE INTO admins(user_id,username) VALUES(?,?)",
        (uid, username)
    )
    c.commit()
    c.close()


def remove_admin(uid: int):
    c = _conn()
    c.execute("DELETE FROM admins WHERE user_id=?", (uid,))
    c.commit()
    c.close()


def get_admins() -> list[dict]:
    c = _conn()
    rows = c.execute("SELECT * FROM admins").fetchall()
    c.close()
    return [dict(r) for r in rows]


def stats() -> dict:
    c = _conn()
    s = {}
    for key, sql in [
        ("users",   "SELECT COUNT(*) FROM users"),
        ("orders",  "SELECT COUNT(*) FROM orders"),
        ("done",    "SELECT COUNT(*) FROM orders WHERE status='completed'"),
        ("pending", "SELECT COUNT(*) FROM orders WHERE status IN ('pending','processing')"),
        ("revenue", "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE kind='credit'"),
        ("spent",   "SELECT COALESCE(SUM(total_spent),0) FROM users"),
        ("banned",  "SELECT COUNT(*) FROM users WHERE is_banned=1"),
        ("tools",   "SELECT COUNT(*) FROM orders WHERE service_id='TOOL' AND status='completed'"),
    ]:
        s[key] = c.execute(sql).fetchone()[0]
    c.close()
    return s


def get_tx_history(uid: int, limit: int = 10) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM transactions WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
        (uid, limit)
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]