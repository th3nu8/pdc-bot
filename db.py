import sqlite3
from datetime import datetime, timezone

DB_PATH = "vp_data.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        vp INTEGER NOT NULL DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        reason TEXT,
        admin_id INTEGER,
        timestamp TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS monthly_checks (
        month_key TEXT PRIMARY KEY,
        run_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS awards_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        username TEXT,
        award_name TEXT NOT NULL,
        reason TEXT,
        admin_id INTEGER,
        timestamp TEXT
    )""")
    conn.commit()
    conn.close()


def _ensure_user(c, user_id, username):
    c.execute("INSERT OR IGNORE INTO users (user_id, username, vp) VALUES (?, ?, 0)", (user_id, username))
    c.execute("UPDATE users SET username=? WHERE user_id=?", (username, user_id))


def add_vp(user_id, username, amount, reason, admin_id):
    """Adds (or subtracts, if amount is negative) VP and logs a transaction. Returns new total."""
    conn = get_conn()
    c = conn.cursor()
    _ensure_user(c, user_id, username)
    c.execute("UPDATE users SET vp = vp + ? WHERE user_id=?", (amount, user_id))
    c.execute(
        "INSERT INTO transactions (user_id, amount, reason, admin_id, timestamp) VALUES (?,?,?,?,?)",
        (user_id, amount, reason, admin_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    c.execute("SELECT vp FROM users WHERE user_id=?", (user_id,))
    total = c.fetchone()[0]
    conn.close()
    return total


def set_vp(user_id, username, amount, reason, admin_id):
    """Sets VP to an absolute value and logs the delta as a transaction. Returns new total."""
    conn = get_conn()
    c = conn.cursor()
    _ensure_user(c, user_id, username)
    c.execute("SELECT vp FROM users WHERE user_id=?", (user_id,))
    current = c.fetchone()[0]
    delta = amount - current
    c.execute("UPDATE users SET vp = ? WHERE user_id=?", (amount, user_id))
    c.execute(
        "INSERT INTO transactions (user_id, amount, reason, admin_id, timestamp) VALUES (?,?,?,?,?)",
        (user_id, delta, f"[SET to {amount}] {reason}", admin_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return amount


def get_vp(user_id, username=None):
    conn = get_conn()
    c = conn.cursor()
    if username:
        _ensure_user(c, user_id, username)
        conn.commit()
    c.execute("SELECT vp FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0


def get_leaderboard(limit=10):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT user_id, username, vp FROM users ORDER BY vp DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return rows


def get_history(user_id=None, limit=10):
    conn = get_conn()
    c = conn.cursor()
    if user_id:
        c.execute(
            "SELECT user_id, amount, reason, admin_id, timestamp FROM transactions "
            "WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
    else:
        c.execute(
            "SELECT user_id, amount, reason, admin_id, timestamp FROM transactions "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        )
    rows = c.fetchall()
    conn.close()
    return rows


def get_vp_earned_in_range(user_id, start_iso, end_iso):
    """Sum of positive (earned) VP for a user within [start_iso, end_iso)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM transactions "
        "WHERE user_id=? AND amount > 0 AND timestamp >= ? AND timestamp < ?",
        (user_id, start_iso, end_iso),
    )
    total = c.fetchone()[0]
    conn.close()
    return total


def has_monthly_check_run(month_key):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT 1 FROM monthly_checks WHERE month_key=?", (month_key,))
    row = c.fetchone()
    conn.close()
    return row is not None


def mark_monthly_check_run(month_key):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO monthly_checks (month_key, run_at) VALUES (?, ?)",
        (month_key, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def add_award(user_id, username, award_name, reason, admin_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO awards_log (user_id, username, award_name, reason, admin_id, timestamp) VALUES (?,?,?,?,?,?)",
        (user_id, username, award_name, reason, admin_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def has_award(user_id, award_name):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM awards_log WHERE user_id=? AND award_name=?", (user_id, award_name))
    count = c.fetchone()[0]
    conn.close()
    return count > 0


def count_award(user_id, award_name):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM awards_log WHERE user_id=? AND award_name=?", (user_id, award_name))
    count = c.fetchone()[0]
    conn.close()
    return count


def get_awards(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT award_name, reason, admin_id, timestamp FROM awards_log WHERE user_id=? ORDER BY id DESC",
        (user_id,),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def remove_last_award(user_id, award_name):
    """Deletes the single most recent award_log entry matching user+award. Returns True if one was removed."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT id FROM awards_log WHERE user_id=? AND award_name=? ORDER BY id DESC LIMIT 1",
        (user_id, award_name),
    )
    row = c.fetchone()
    if not row:
        conn.close()
        return False
    c.execute("DELETE FROM awards_log WHERE id=?", (row[0],))
    conn.commit()
    conn.close()
    return True
