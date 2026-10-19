"""SQLite data layer for the budget app.

Single file database, no ORM, no migrations framework: schema is created on
first run and patched forward with tiny `ensure_column` calls.
"""
import json
import os
import sqlite3
import time

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "budget.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    color       TEXT NOT NULL DEFAULT '#8b8b8b',
    kind        TEXT NOT NULL DEFAULT 'life',
    sort        INTEGER NOT NULL DEFAULT 0,
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    time        TEXT,
    amount      REAL NOT NULL,
    category_id INTEGER REFERENCES categories(id),
    note        TEXT NOT NULL DEFAULT '',
    tags        TEXT NOT NULL DEFAULT '[]',
    budget_id   INTEGER REFERENCES budgets(id),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_date ON records(date);

CREATE TABLE IF NOT EXISTS budgets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    amount      REAL NOT NULL,
    category_id INTEGER REFERENCES categories(id),
    tag         TEXT,
    rule        TEXT NOT NULL,
    start_date  TEXT NOT NULL,
    end_date    TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    category_id    INTEGER REFERENCES categories(id),
    default_amount REAL,
    default_time   TEXT,
    default_tags   TEXT NOT NULL DEFAULT '[]',
    created_at     TEXT NOT NULL
);
"""

DEFAULT_CATEGORIES = [
    ("生活", "#5b8def", "life", 10),
    ("工作", "#e08a3c", "work", 20),
    ("交通", "#41a98a", "life", 30),
    ("餐饮", "#c95f6a", "life", 40),
    ("娱乐", "#8b6fd0", "life", 50),
    ("其他", "#8b8b8b", "other", 90),
]


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init():
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _ensure_column(conn, "records", "budget_id", "INTEGER")
        count = conn.execute("SELECT COUNT(*) AS c FROM categories").fetchone()["c"]
        if count == 0:
            for name, color, kind, sort in DEFAULT_CATEGORIES:
                conn.execute(
                    "INSERT INTO categories (name, color, kind, sort, created_at) VALUES (?,?,?,?,?)",
                    (name, color, kind, sort, now()),
                )
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn, table, column, decl):
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table).fetchall()]
    if column not in cols:
        conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, decl))


# ---------------------------------------------------------------- categories
def list_categories(conn, include_archived=False):
    sql = "SELECT * FROM categories"
    if not include_archived:
        sql += " WHERE archived = 0"
    sql += " ORDER BY sort, id"
    return [dict(r) for r in conn.execute(sql).fetchall()]


def get_category_by_name(conn, name):
    row = conn.execute("SELECT * FROM categories WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def create_category(conn, name, color="#8b8b8b", kind="life", sort=100):
    cur = conn.execute(
        "INSERT INTO categories (name, color, kind, sort, created_at) VALUES (?,?,?,?,?)",
        (name, color, kind, sort, now()),
    )
    conn.commit()
    return get_category(conn, cur.lastrowid)


def get_category(conn, cid):
    row = conn.execute("SELECT * FROM categories WHERE id = ?", (cid,)).fetchone()
    return dict(row) if row else None


def update_category(conn, cid, patch):
    fields, values = _pick(patch, ["name", "color", "kind", "sort", "archived"])
    if not fields:
        return get_category(conn, cid)
    conn.execute(
        "UPDATE categories SET %s WHERE id = ?" % ", ".join("%s = ?" % f for f in fields),
        values + [cid],
    )
    conn.commit()
    return get_category(conn, cid)


def delete_category(conn, cid):
    conn.execute("UPDATE records SET category_id = NULL WHERE category_id = ?", (cid,))
    conn.execute("UPDATE budgets SET category_id = NULL WHERE category_id = ?", (cid,))
    conn.execute("UPDATE profiles SET category_id = NULL WHERE category_id = ?", (cid,))
    conn.execute("DELETE FROM categories WHERE id = ?", (cid,))
    conn.commit()


def resolve_category(conn, ref, create=True):
    """Resolve a category by id, name, or auto-create it."""
    if ref is None or ref == "":
        return None
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        return get_category(conn, int(ref))
    cat = get_category_by_name(conn, str(ref).strip())
    if cat:
        return cat
    if create:
        return create_category(conn, str(ref).strip())
    return None


# ------------------------------------------------------------------- records
def _row_to_record(row):
    rec = dict(row)
    try:
        rec["tags"] = json.loads(rec.get("tags") or "[]")
    except ValueError:
        rec["tags"] = []
    return rec


def list_records(conn, date=None, start=None, end=None, category_id=None, limit=None):
    sql = (
        "SELECT r.*, c.name AS category_name, c.color AS category_color "
        "FROM records r LEFT JOIN categories c ON c.id = r.category_id WHERE 1=1"
    )
    args = []
    if date:
        sql += " AND r.date = ?"
        args.append(date)
    if start:
        sql += " AND r.date >= ?"
        args.append(start)
    if end:
        sql += " AND r.date <= ?"
        args.append(end)
    if category_id:
        sql += " AND r.category_id = ?"
        args.append(category_id)
    sql += " ORDER BY r.date DESC, COALESCE(r.time, '') DESC, r.id DESC"
    if limit:
        sql += " LIMIT ?"
        args.append(limit)
    return [_row_to_record(r) for r in conn.execute(sql, args).fetchall()]


def get_record(conn, rid):
    row = conn.execute(
        "SELECT r.*, c.name AS category_name, c.color AS category_color "
        "FROM records r LEFT JOIN categories c ON c.id = r.category_id WHERE r.id = ?",
        (rid,),
    ).fetchone()
    return _row_to_record(row) if row else None


def create_record(conn, date, amount, note="", category_id=None, tags=None, tm=None, budget_id=None):
    ts = now()
    cur = conn.execute(
        "INSERT INTO records (date, time, amount, category_id, note, tags, budget_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (date, tm, round(float(amount), 2), category_id, note, json.dumps(tags or [], ensure_ascii=False),
         budget_id, ts, ts),
    )
    conn.commit()
    return get_record(conn, cur.lastrowid)


def update_record(conn, rid, patch):
    fields, values = _pick(patch, ["date", "time", "amount", "category_id", "note", "tags", "budget_id"])
    if not fields:
        return get_record(conn, rid)
    if "tags" in fields:
        idx = fields.index("tags")
        values[idx] = json.dumps(values[idx] or [], ensure_ascii=False)
    if "amount" in fields:
        idx = fields.index("amount")
        values[idx] = round(float(values[idx]), 2)
    fields.append("updated_at")
    values.append(now())
    conn.execute(
        "UPDATE records SET %s WHERE id = ?" % ", ".join("%s = ?" % f for f in fields),
        values + [rid],
    )
    conn.commit()
    return get_record(conn, rid)


def delete_record(conn, rid):
    conn.execute("DELETE FROM records WHERE id = ?", (rid,))
    conn.commit()


# ------------------------------------------------------------------- budgets
def _row_to_budget(row):
    b = dict(row)
    try:
        b["rule"] = json.loads(b.get("rule") or "{}")
    except ValueError:
        b["rule"] = {}
    return b


def list_budgets(conn, active_only=False):
    sql = (
        "SELECT b.*, c.name AS category_name, c.color AS category_color "
        "FROM budgets b LEFT JOIN categories c ON c.id = b.category_id"
    )
    if active_only:
        sql += " WHERE b.active = 1"
    sql += " ORDER BY b.id DESC"
    return [_row_to_budget(r) for r in conn.execute(sql).fetchall()]


def get_budget(conn, bid):
    row = conn.execute(
        "SELECT b.*, c.name AS category_name, c.color AS category_color "
        "FROM budgets b LEFT JOIN categories c ON c.id = b.category_id WHERE b.id = ?",
        (bid,),
    ).fetchone()
    return _row_to_budget(row) if row else None


def create_budget(conn, title, amount, rule, start_date, category_id=None, tag=None,
                  end_date=None, active=1):
    cur = conn.execute(
        "INSERT INTO budgets (title, amount, category_id, tag, rule, start_date, end_date, active, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (title, round(float(amount), 2), category_id, tag,
         json.dumps(rule, ensure_ascii=False), start_date, end_date, active, now()),
    )
    conn.commit()
    return get_budget(conn, cur.lastrowid)


def update_budget(conn, bid, patch):
    fields, values = _pick(patch, ["title", "amount", "category_id", "tag", "rule",
                                   "start_date", "end_date", "active"])
    if not fields:
        return get_budget(conn, bid)
    if "rule" in fields:
        idx = fields.index("rule")
        values[idx] = json.dumps(values[idx] or {}, ensure_ascii=False)
    if "amount" in fields:
        idx = fields.index("amount")
        values[idx] = round(float(values[idx]), 2)
    conn.execute(
        "UPDATE budgets SET %s WHERE id = ?" % ", ".join("%s = ?" % f for f in fields),
        values + [bid],
    )
    conn.commit()
    return get_budget(conn, bid)


def delete_budget(conn, bid):
    conn.execute("UPDATE records SET budget_id = NULL WHERE budget_id = ?", (bid,))
    conn.execute("DELETE FROM budgets WHERE id = ?", (bid,))
    conn.commit()


# ------------------------------------------------------------------ profiles
def _row_to_profile(row):
    p = dict(row)
    try:
        p["default_tags"] = json.loads(p.get("default_tags") or "[]")
    except ValueError:
        p["default_tags"] = []
    return p


def list_profiles(conn):
    rows = conn.execute(
        "SELECT p.*, c.name AS category_name, c.color AS category_color "
        "FROM profiles p LEFT JOIN categories c ON c.id = p.category_id ORDER BY p.id"
    ).fetchall()
    return [_row_to_profile(r) for r in rows]


def get_profile(conn, pid):
    row = conn.execute(
        "SELECT p.*, c.name AS category_name, c.color AS category_color "
        "FROM profiles p LEFT JOIN categories c ON c.id = p.category_id WHERE p.id = ?",
        (pid,),
    ).fetchone()
    return _row_to_profile(row) if row else None


def create_profile(conn, name, category_id=None, default_amount=None,
                   default_time=None, default_tags=None):
    cur = conn.execute(
        "INSERT INTO profiles (name, category_id, default_amount, default_time, default_tags, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (name, category_id, default_amount, default_time,
         json.dumps(default_tags or [], ensure_ascii=False), now()),
    )
    conn.commit()
    return get_profile(conn, cur.lastrowid)


def update_profile(conn, pid, patch):
    fields, values = _pick(patch, ["name", "category_id", "default_amount",
                                   "default_time", "default_tags"])
    if not fields:
        return get_profile(conn, pid)
    if "default_tags" in fields:
        idx = fields.index("default_tags")
        values[idx] = json.dumps(values[idx] or [], ensure_ascii=False)
    conn.execute(
        "UPDATE profiles SET %s WHERE id = ?" % ", ".join("%s = ?" % f for f in fields),
        values + [pid],
    )
    conn.commit()
    return get_profile(conn, pid)


def delete_profile(conn, pid):
    conn.execute("DELETE FROM profiles WHERE id = ?", (pid,))
    conn.commit()


# -------------------------------------------------------------------- helper
def _pick(patch, allowed):
    fields, values = [], []
    for f in allowed:
        if f in patch:
            fields.append(f)
            values.append(patch[f])
    return fields, values
