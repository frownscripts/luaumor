import os
import sqlite3
import hashlib
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "delta.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, definition: str):
    if column_name not in _table_columns(conn, table_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def _ensure_setting(conn: sqlite3.Connection, key: str, value: str):
    exists = conn.execute("SELECT 1 FROM settings WHERE key = ?", (key,)).fetchone()
    if not exists:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def utcnow_iso() -> str:
    return datetime.utcnow().isoformat()


def compute_discord_link_deadline(user: dict) -> Optional[str]:
    deadline = user.get("discord_link_required_by")
    if deadline:
        return deadline
    created_at = parse_dt(user.get("created_at"))
    if not created_at:
        return None
    return (created_at + timedelta(hours=24)).isoformat()


def is_user_inactive_pending_discord(user: Optional[dict]) -> bool:
    if not user or user.get("role") == "admin":
        return False
    if user.get("discord_id"):
        return False
    deadline = parse_dt(compute_discord_link_deadline(user))
    if not deadline:
        return False
    return datetime.utcnow() > deadline


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT,
            discord_id TEXT UNIQUE,
            discord_username TEXT,
            discord_avatar TEXT,
            email TEXT,
            role TEXT DEFAULT 'user',
            created_at TEXT DEFAULT (datetime('now')),
            banned INTEGER DEFAULT 0,
            ban_reason TEXT,
            active_key_id INTEGER,
            FOREIGN KEY (active_key_id) REFERENCES keys(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_code TEXT UNIQUE NOT NULL,
            plan TEXT NOT NULL,
            duration_days INTEGER,
            used INTEGER DEFAULT 0,
            used_by INTEGER,
            used_at TEXT,
            expires_at TEXT,
            active INTEGER DEFAULT 1,
            paused INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            note TEXT,
            FOREIGN KEY (used_by) REFERENCES users(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL NOT NULL,
            status TEXT DEFAULT 'pending',
            plan TEXT,
            key_id INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            category TEXT DEFAULT 'update',
            author TEXT DEFAULT 'Admin',
            created_at TEXT DEFAULT (datetime('now')),
            pinned INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_jti TEXT UNIQUE NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            category TEXT DEFAULT 'support',
            status TEXT DEFAULT 'open',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            closed_at TEXT,
            closed_by INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (closed_by) REFERENCES users(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS ticket_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            user_id INTEGER,
            author_role TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    _ensure_column(conn, "users", "discord_link_required_by", "TEXT")
    _ensure_column(conn, "users", "last_login_at", "TEXT")
    _ensure_column(conn, "users", "download_count", "INTEGER DEFAULT 0")

    conn.execute("""
        UPDATE users
        SET discord_link_required_by = COALESCE(discord_link_required_by, datetime(created_at, '+24 hours'))
        WHERE role != 'admin'
    """)
    _ensure_setting(conn, "download_count", "0")
    conn.commit()

    admin = c.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
    if not admin:
        pw = hash_password("Admin@Delta2026!")
        c.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
            ("admin", pw),
        )
        conn.commit()
        print("[DB] Default admin created: admin / Admin@Delta2026!")

    conn.close()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000)
    return f"{salt}:{hashed.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, hashed = stored.split(":", 1)
        new_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000)
        return secrets.compare_digest(new_hash.hex(), hashed)
    except Exception:
        return False


def generate_key(plan: str) -> str:
    chars = string.ascii_uppercase + string.digits
    parts = ["".join(secrets.choice(chars) for _ in range(4)) for _ in range(4)]
    if plan == "monthly":
        prefix = "DELTAM"
    elif plan == "lifetime":
        prefix = "DELTAL"
    else:
        prefix = "DELTAT"
    return f"{prefix}-{'-'.join(parts)}"


def create_keys_bulk(plan: str, count: int, note: str = "") -> list:
    conn = get_db()
    c = conn.cursor()
    generated = []
    duration = 30 if plan == "monthly" else None
    for _ in range(count):
        key_code = generate_key(plan)
        while c.execute("SELECT id FROM keys WHERE key_code = ?", (key_code,)).fetchone():
            key_code = generate_key(plan)
        c.execute(
            "INSERT INTO keys (key_code, plan, duration_days, note) VALUES (?, ?, ?, ?)",
            (key_code, plan, duration, note),
        )
        generated.append(key_code)
    conn.commit()
    conn.close()
    return generated


def create_test_keys_bulk(duration_seconds: int, count: int, note: str = "") -> list:
    conn = get_db()
    c = conn.cursor()
    generated = []
    for _ in range(count):
        key_code = generate_key("test")
        while c.execute("SELECT id FROM keys WHERE key_code = ?", (key_code,)).fetchone():
            key_code = generate_key("test")
        c.execute(
            "INSERT INTO keys (key_code, plan, duration_days, note) VALUES (?, 'test', ?, ?)",
            (key_code, None, note or f"test-{duration_seconds}s"),
        )
        c.execute(
            "UPDATE keys SET note=? WHERE key_code=?",
            (f"__test_secs_{duration_seconds}__" + (note or ""), key_code),
        )
        generated.append(key_code)
    conn.commit()
    conn.close()
    return generated


def get_user_by_id(user_id: int):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(user) if user else None


def get_user_by_username(username: str):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return dict(user) if user else None


def get_user_by_discord_id(discord_id: str):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE discord_id = ?", (discord_id,)).fetchone()
    conn.close()
    return dict(user) if user else None


def merge_user_accounts(source_user_id: int, target_user_id: int):
    if source_user_id == target_user_id:
        return {"success": True, "transferred_key": False}

    conn = get_db()
    c = conn.cursor()
    try:
        source = c.execute("SELECT * FROM users WHERE id = ?", (source_user_id,)).fetchone()
        target = c.execute("SELECT * FROM users WHERE id = ?", (target_user_id,)).fetchone()
        if not source or not target:
            return {"success": False, "error": "Account not found"}

        source = dict(source)
        target = dict(target)
        if source.get("active_key_id") and target.get("active_key_id") and source["active_key_id"] != target["active_key_id"]:
            return {"success": False, "error": "Both accounts already have different plans"}

        transferred_key = False
        if source.get("active_key_id") and not target.get("active_key_id"):
            c.execute("UPDATE users SET active_key_id = ? WHERE id = ?", (source["active_key_id"], target_user_id))
            transferred_key = True

        c.execute("UPDATE keys SET used_by = ? WHERE used_by = ?", (target_user_id, source_user_id))
        c.execute("UPDATE transactions SET user_id = ? WHERE user_id = ?", (target_user_id, source_user_id))
        c.execute("UPDATE sessions SET user_id = ? WHERE user_id = ?", (target_user_id, source_user_id))
        c.execute("UPDATE tickets SET user_id = ? WHERE user_id = ?", (target_user_id, source_user_id))
        c.execute("UPDATE ticket_messages SET user_id = ? WHERE user_id = ?", (target_user_id, source_user_id))
        c.execute("DELETE FROM users WHERE id = ?", (source_user_id,))
        conn.commit()
        return {"success": True, "transferred_key": transferred_key}
    finally:
        conn.close()


def get_active_key_for_user(user_id: int):
    conn = get_db()
    key = conn.execute(
        """
        SELECT k.* FROM keys k
        JOIN users u ON u.active_key_id = k.id
        WHERE u.id = ? AND k.active = 1 AND k.paused = 0
        """,
        (user_id,),
    ).fetchone()
    conn.close()
    if not key:
        return None
    k = dict(key)
    if k["expires_at"]:
        expires = parse_dt(k["expires_at"])
        if expires and expires < datetime.utcnow():
            return None
    return k


def is_key_valid_for_user(user_id: int) -> bool:
    return get_active_key_for_user(user_id) is not None


def redeem_key(user_id: int, key_code: str):
    conn = get_db()
    c = conn.cursor()
    key = c.execute("SELECT * FROM keys WHERE key_code = ? AND used = 0 AND active = 1", (key_code,)).fetchone()
    if not key:
        conn.close()
        return {"success": False, "error": "Invalid or already used key"}

    key = dict(key)
    now = datetime.utcnow()
    expires_at = None
    if key["plan"] == "test" and key.get("note", "").startswith("__test_secs_"):
        try:
            secs_part = key["note"].split("__test_secs_")[1].split("__")[0]
            expires_at = (now + timedelta(seconds=int(secs_part))).isoformat()
        except Exception:
            expires_at = (now + timedelta(seconds=30)).isoformat()
    elif key["duration_days"]:
        expires_at = (now + timedelta(days=key["duration_days"])).isoformat()

    c.execute(
        "UPDATE keys SET used = 1, used_by = ?, used_at = ?, expires_at = ? WHERE id = ?",
        (user_id, now.isoformat(), expires_at, key["id"]),
    )
    c.execute("UPDATE users SET active_key_id = ? WHERE id = ?", (key["id"], user_id))

    amount = 1.00 if key["plan"] == "monthly" else (5.00 if key["plan"] == "lifetime" else 0.00)
    c.execute(
        "INSERT INTO transactions (user_id, amount, status, plan, key_id) VALUES (?, ?, 'completed', ?, ?)",
        (user_id, amount, key["plan"], key["id"]),
    )
    conn.commit()
    conn.close()
    return {"success": True, "plan": key["plan"], "expires_at": expires_at}


def store_session(user_id: int, token_jti: str, expires_at: str):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO sessions (user_id, token_jti, expires_at) VALUES (?, ?, ?)",
        (user_id, token_jti, expires_at),
    )
    conn.commit()
    conn.close()


def is_session_valid(token_jti: str) -> bool:
    conn = get_db()
    row = conn.execute("SELECT expires_at FROM sessions WHERE token_jti = ?", (token_jti,)).fetchone()
    conn.close()
    if not row:
        return False
    expires_at = parse_dt(row["expires_at"])
    return bool(expires_at and expires_at > datetime.utcnow())


def cleanup_sessions():
    conn = get_db()
    conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (utcnow_iso(),))
    conn.commit()
    conn.close()


def set_last_login(user_id: int):
    conn = get_db()
    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (utcnow_iso(), user_id))
    conn.commit()
    conn.close()


def get_download_count() -> int:
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key = 'download_count'").fetchone()
    conn.close()
    return int(row["value"] if row else 0)


def increment_download_count() -> int:
    conn = get_db()
    conn.execute(
        "UPDATE settings SET value = CAST(COALESCE(value, '0') AS INTEGER) + 1, updated_at = ? WHERE key = 'download_count'",
        (utcnow_iso(),),
    )
    conn.commit()
    row = conn.execute("SELECT value FROM settings WHERE key = 'download_count'").fetchone()
    conn.close()
    return int(row["value"] if row else 0)


def increment_user_download_count(user_id: int) -> int:
    conn = get_db()
    conn.execute(
        "UPDATE users SET download_count = COALESCE(download_count, 0) + 1 WHERE id = ?",
        (user_id,),
    )
    conn.commit()
    row = conn.execute("SELECT download_count FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return int(row["download_count"] if row else 0)


def get_active_user_count(hours: int = 24) -> int:
    threshold = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    conn = get_db()
    row = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM users
        WHERE role != 'admin'
          AND banned = 0
          AND last_login_at IS NOT NULL
          AND last_login_at >= ?
        """,
        (threshold,),
    ).fetchone()
    conn.close()
    return int(row["total"] if row else 0)


def get_latest_announcement() -> Optional[dict]:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM updates WHERE category = 'announcement' ORDER BY pinned DESC, created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_public_stats() -> dict:
    return {"download_count": get_download_count(), "latest_announcement": get_latest_announcement()}


def get_user_dashboard(user_id: int):
    conn = get_db()
    user_row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user_row:
        conn.close()
        raise ValueError("User not found")
    user = dict(user_row)
    transactions = [
        dict(t)
        for t in conn.execute(
            "SELECT * FROM transactions WHERE user_id = ? ORDER BY created_at DESC LIMIT 25",
            (user_id,),
        ).fetchall()
    ]
    total_spent = conn.execute(
        "SELECT COALESCE(SUM(amount),0) as total FROM transactions WHERE user_id = ? AND status='completed'",
        (user_id,),
    ).fetchone()["total"]
    conn.close()

    active_key = get_active_key_for_user(user_id)
    subscription_active = active_key is not None
    needs_discord_link = is_user_inactive_pending_discord(user)
    account_active = not needs_discord_link

    plan_name = None
    expires_at = None
    key_plan_raw = None
    if active_key:
        key_plan_raw = active_key["plan"]
        if key_plan_raw == "monthly":
            plan_name = "Delta iOS Premium — Monthly"
        elif key_plan_raw == "lifetime":
            plan_name = "Delta iOS Premium — Lifetime"
        else:
            plan_name = "Delta iOS Premium — Test"
        expires_at = active_key.get("expires_at")

    return {
        "username": user["username"],
        "discord_username": user.get("discord_username"),
        "discord_avatar": user.get("discord_avatar"),
        "discord_id": user.get("discord_id"),
        "role": user["role"],
        "created_at": user.get("created_at"),
        "access_active": subscription_active and account_active,
        "subscription_active": subscription_active,
        "account_active": account_active,
        "needs_discord_link": needs_discord_link,
        "discord_link_required_by": compute_discord_link_deadline(user),
        "account_notice": "Link your Discord account to reactivate premium access." if needs_discord_link else None,
        "plan_name": plan_name,
        "plan_raw": key_plan_raw,
        "expires_at": expires_at,
        "transactions": transactions,
        "total_spent": float(total_spent or 0),
        "transaction_count": len(transactions),
        "download_count": get_download_count(),
        "personal_download_count": int(user.get("download_count") or 0),
        "active_users_24h": get_active_user_count(),
        "latest_announcement": get_latest_announcement(),
    }


def list_role_sync_users() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT u.*, k.plan, k.expires_at, k.active AS key_active, k.paused AS key_paused
        FROM users u
        LEFT JOIN keys k ON k.id = u.active_key_id
        WHERE u.discord_id IS NOT NULL
        """
    ).fetchall()
    conn.close()

    out = []
    for row in rows:
        user = dict(row)
        expires_at = parse_dt(user.get("expires_at"))
        has_active_plan = bool(
            user.get("plan")
            and user.get("key_active") == 1
            and user.get("key_paused") == 0
            and (expires_at is None or expires_at > datetime.utcnow())
        )
        out.append({
            "user_id": user["id"],
            "discord_id": user.get("discord_id"),
            "plan": user.get("plan"),
            "should_have_role": has_active_plan and not is_user_inactive_pending_discord(user),
        })
    return out


def create_ticket(user_id: int, subject: str, message: str, category: str = "support") -> dict:
    conn = get_db()
    c = conn.cursor()
    now = utcnow_iso()
    c.execute(
        "INSERT INTO tickets (user_id, subject, category, status, created_at, updated_at) VALUES (?, ?, ?, 'open', ?, ?)",
        (user_id, subject.strip(), category.strip() or "support", now, now),
    )
    ticket_id = c.lastrowid
    c.execute(
        "INSERT INTO ticket_messages (ticket_id, user_id, author_role, message, created_at) VALUES (?, ?, 'user', ?, ?)",
        (ticket_id, user_id, message.strip(), now),
    )
    conn.commit()
    conn.close()
    return get_ticket(ticket_id, requester_user_id=user_id, is_admin=True)


def add_ticket_message(ticket_id: int, user_id: int, author_role: str, message: str) -> dict:
    conn = get_db()
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError("Ticket not found")
    ticket = dict(row)
    if ticket["status"] != "open":
        conn.close()
        raise ValueError("Ticket is closed")
    now = utcnow_iso()
    conn.execute(
        "INSERT INTO ticket_messages (ticket_id, user_id, author_role, message, created_at) VALUES (?, ?, ?, ?, ?)",
        (ticket_id, user_id, author_role, message.strip(), now),
    )
    conn.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (now, ticket_id))
    conn.commit()
    conn.close()
    return get_ticket(ticket_id, requester_user_id=user_id, is_admin=True)


def close_ticket(ticket_id: int, admin_id: int) -> dict:
    conn = get_db()
    now = utcnow_iso()
    conn.execute(
        "UPDATE tickets SET status='closed', updated_at=?, closed_at=?, closed_by=? WHERE id=?",
        (now, now, admin_id, ticket_id),
    )
    conn.commit()
    conn.close()
    return get_ticket(ticket_id, requester_user_id=admin_id, is_admin=True)


def list_tickets_for_user(user_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT t.*,
               (SELECT COUNT(*) FROM ticket_messages tm WHERE tm.ticket_id = t.id) AS message_count,
               (SELECT tm.message FROM ticket_messages tm WHERE tm.ticket_id = t.id ORDER BY tm.id DESC LIMIT 1) AS last_message,
               (SELECT tm.created_at FROM ticket_messages tm WHERE tm.ticket_id = t.id ORDER BY tm.id DESC LIMIT 1) AS last_message_at
        FROM tickets t
        WHERE t.user_id = ?
        ORDER BY CASE WHEN t.status = 'open' THEN 0 ELSE 1 END, t.updated_at DESC, t.id DESC
        """,
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_tickets_admin() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT t.*, u.username,
               (SELECT COUNT(*) FROM ticket_messages tm WHERE tm.ticket_id = t.id) AS message_count,
               (SELECT tm.message FROM ticket_messages tm WHERE tm.ticket_id = t.id ORDER BY tm.id DESC LIMIT 1) AS last_message,
               (SELECT tm.created_at FROM ticket_messages tm WHERE tm.ticket_id = t.id ORDER BY tm.id DESC LIMIT 1) AS last_message_at
        FROM tickets t
        JOIN users u ON u.id = t.user_id
        ORDER BY CASE WHEN t.status = 'open' THEN 0 ELSE 1 END, t.updated_at DESC, t.id DESC
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_ticket(ticket_id: int, requester_user_id: Optional[int] = None, is_admin: bool = False) -> Optional[dict]:
    conn = get_db()
    row = conn.execute(
        "SELECT t.*, u.username FROM tickets t JOIN users u ON u.id = t.user_id WHERE t.id = ?",
        (ticket_id,),
    ).fetchone()
    if not row:
        conn.close()
        return None
    ticket = dict(row)
    if not is_admin and requester_user_id is not None and ticket["user_id"] != requester_user_id:
        conn.close()
        return None
    messages = [
        dict(m)
        for m in conn.execute(
            "SELECT tm.*, u.username FROM ticket_messages tm LEFT JOIN users u ON u.id = tm.user_id WHERE tm.ticket_id = ? ORDER BY tm.id ASC",
            (ticket_id,),
        ).fetchall()
    ]
    conn.close()
    ticket["messages"] = messages
    return ticket
