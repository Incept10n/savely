import os
from datetime import datetime

import pymysql
import pymysql.cursors
from flask import current_app

SCHEMA = """
CREATE TABLE IF NOT EXISTS spends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    amount REAL NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    spent_at DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""

MYSQL_SCHEMA = """
CREATE TABLE IF NOT EXISTS spends (
    id INT AUTO_INCREMENT PRIMARY KEY,
    amount DECIMAL(12,2) NOT NULL,
    comment VARCHAR(500) NOT NULL DEFAULT '',
    spent_at DATETIME NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

AI_USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_rub REAL NOT NULL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""

MYSQL_AI_USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_usage (
    id INT AUTO_INCREMENT PRIMARY KEY,
    input_tokens INT NOT NULL DEFAULT 0,
    output_tokens INT NOT NULL DEFAULT 0,
    cost_rub DECIMAL(12,4) NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


SPEND_CATEGORIES_SCHEMA = """
CREATE TABLE IF NOT EXISTS spend_categories (
    spend_id INTEGER PRIMARY KEY,
    category TEXT NOT NULL,
    confidence REAL,
    cosine REAL,
    source TEXT NOT NULL DEFAULT 'knn',
    classifier_rev TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""

MYSQL_SPEND_CATEGORIES_SCHEMA = """
CREATE TABLE IF NOT EXISTS spend_categories (
    spend_id INT PRIMARY KEY,
    category VARCHAR(64) NOT NULL,
    confidence DECIMAL(5,2) NULL,
    cosine DECIMAL(6,4) NULL,
    source VARCHAR(16) NOT NULL DEFAULT 'knn',
    classifier_rev VARCHAR(64) NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def _is_sqlite():
    return current_app.config["DB_TYPE"] == "sqlite"


def _connection():
    if _is_sqlite():
        import sqlite3

        conn = sqlite3.connect(current_app.config["DB_FILE"])
        conn.row_factory = sqlite3.Row

        def _adapt_datetime(value):
            return value.isoformat(sep=" ")

        sqlite3.register_adapter(datetime, _adapt_datetime)
        return conn
    return pymysql.connect(
        host=current_app.config["DB_HOST"],
        port=current_app.config["DB_PORT"],
        user=current_app.config["DB_USER"],
        password=current_app.config["DB_PASSWORD"],
        database=current_app.config["DB_NAME"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def _execute(conn, query, params=()):
    cur = conn.cursor()
    if not _is_sqlite():
        query = query.replace("?", "%s")
    cur.execute(query, params)
    return cur


def list_spends(conn=None):
    close = conn is None
    if conn is None:
        conn = _connection()
    try:
        cur = _execute(
            conn, "SELECT id, amount, comment, spent_at FROM spends ORDER BY spent_at DESC, id DESC"
        )
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]
    finally:
        if close:
            conn.close()


def list_spends_month(start, end, conn=None):
    # Month-scoped read: `spent_at` is naive local time, so both bounds are
    # local datetimes too. Avoids pulling the whole history table to filter it
    # in Python.
    close = conn is None
    if conn is None:
        conn = _connection()
    try:
        cur = _execute(
            conn,
            "SELECT id, amount, comment, spent_at FROM spends "
            "WHERE spent_at >= ? AND spent_at < ? ORDER BY spent_at DESC, id DESC",
            (start, end),
        )
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]
    finally:
        if close:
            conn.close()


def get_spend(spend_id, conn=None):
    close = conn is None
    if conn is None:
        conn = _connection()
    try:
        cur = _execute(conn, "SELECT id, amount, comment, spent_at FROM spends WHERE id = ?", (spend_id,))
        row = cur.fetchone()
        cur.close()
        return dict(row) if row else None
    finally:
        if close:
            conn.close()


def create_spend(amount, comment, spent_at, conn=None):
    close = conn is None
    if conn is None:
        conn = _connection()
    try:
        cur = _execute(
            conn, "INSERT INTO spends (amount, comment, spent_at) VALUES (?, ?, ?)",
            (amount, comment, spent_at),
        )
        new_id = cur.lastrowid
        cur.close()
        conn.commit()
        return get_spend(new_id, conn)
    finally:
        if close:
            conn.close()


def update_spend(spend_id, amount, comment, spent_at, conn=None):
    close = conn is None
    if conn is None:
        conn = _connection()
    try:
        cur = _execute(
            conn, "UPDATE spends SET amount = ?, comment = ?, spent_at = ? WHERE id = ?",
            (amount, comment, spent_at, spend_id),
        )
        affected = cur.rowcount
        cur.close()
        conn.commit()
        if affected == 0:
            return None
        return get_spend(spend_id, conn)
    finally:
        if close:
            conn.close()


def delete_spend(spend_id, conn=None):
    close = conn is None
    if conn is None:
        conn = _connection()
    try:
        cur = _execute(conn, "DELETE FROM spends WHERE id = ?", (spend_id,))
        affected = cur.rowcount
        cur.close()
        if affected > 0:
            # No FK cascade: SQLite would need `PRAGMA foreign_keys = ON`,
            # which is one dialect-specific branch too many for one row.
            cur = _execute(
                conn, "DELETE FROM spend_categories WHERE spend_id = ?", (spend_id,)
            )
            cur.close()
        conn.commit()
        return affected > 0
    finally:
        if close:
            conn.close()


def _ensure_index(conn, table, column):
    # SQLite understands CREATE INDEX IF NOT EXISTS, MySQL does not, so the
    # existence is checked up front there. Indexes are pure optimization, so a
    # failure here is logged by the caller and never blocks startup.
    name = f"idx_{table}_{column}"
    if _is_sqlite():
        _execute(conn, f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})")
        return
    cur = _execute(
        conn,
        "SELECT 1 FROM information_schema.statistics "
        "WHERE table_schema = ? AND table_name = ? AND index_name = ? LIMIT 1",
        (current_app.config["DB_NAME"], table, name),
    )
    exists = cur.fetchone() is not None
    cur.close()
    if exists:
        return
    _execute(conn, f"ALTER TABLE {table} ADD INDEX {name} ({column})")


def init_db():
    conn = _connection()
    try:
        _execute(conn, MYSQL_SCHEMA if not _is_sqlite() else SCHEMA)
        _execute(
            conn,
            MYSQL_AI_USAGE_SCHEMA if not _is_sqlite() else AI_USAGE_SCHEMA,
        )
        _execute(
            conn,
            MYSQL_SPEND_CATEGORIES_SCHEMA
            if not _is_sqlite()
            else SPEND_CATEGORIES_SCHEMA,
        )
        try:
            _ensure_index(conn, "spends", "spent_at")
        except Exception:  # noqa: BLE001 - missing ALTER privileges must not crash the app
            conn.rollback()
            current_app.logger.warning(
                "could not ensure index on spends(spent_at)", exc_info=True
            )
        conn.commit()
    finally:
        conn.close()


def record_ai_usage(input_tokens, output_tokens, cost_rub, conn=None):
    close = conn is None
    if conn is None:
        conn = _connection()
    try:
        cur = _execute(
            conn,
            "INSERT INTO ai_usage (input_tokens, output_tokens, cost_rub) VALUES (?, ?, ?)",
            (input_tokens, output_tokens, cost_rub),
        )
        cur.close()
        conn.commit()
    finally:
        if close:
            conn.close()


def get_ai_total_cost(conn=None):
    close = conn is None
    if conn is None:
        conn = _connection()
    try:
        cur = _execute(conn, "SELECT COALESCE(SUM(cost_rub), 0) AS total FROM ai_usage")
        row = cur.fetchone()
        cur.close()
        return float(row["total"]) if row else 0.0
    finally:
        if close:
            conn.close()


def get_ai_cost_since(since_dt, conn=None):
    # Sums AI cost for usage recorded at/after `since_dt` (a datetime). Both
    # SQLite and MySQL store created_at in UTC, so a UTC boundary is compared.
    close = conn is None
    if conn is None:
        conn = _connection()
    try:
        cur = _execute(
            conn,
            "SELECT COALESCE(SUM(cost_rub), 0) AS total FROM ai_usage WHERE created_at >= ?",
            (since_dt,),
        )
        row = cur.fetchone()
        cur.close()
        return float(row["total"]) if row else 0.0
    finally:
        if close:
            conn.close()


# --- spend_categories -------------------------------------------------------
# Categories live in their own table instead of extra columns on `spends`:
# `init_db()` uses CREATE TABLE IF NOT EXISTS, so a new table needs no ALTER on
# the live MySQL instance and no migration step at all.


def _category_row(row):
    if not row:
        return None
    return {
        "spend_id": int(row["spend_id"]),
        "category": row["category"],
        "confidence": float(row["confidence"]) if row["confidence"] is not None else None,
        "cosine": float(row["cosine"]) if row["cosine"] is not None else None,
        "source": row["source"],
    }


def upsert_category(
    spend_id,
    category,
    confidence=None,
    cosine=None,
    source="knn",
    classifier_rev=None,
    conn=None,
):
    close = conn is None
    if conn is None:
        conn = _connection()
    params = (spend_id, category, confidence, cosine, source, classifier_rev)
    try:
        if _is_sqlite():
            _execute(
                conn,
                "INSERT INTO spend_categories "
                "(spend_id, category, confidence, cosine, source, classifier_rev) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(spend_id) DO UPDATE SET "
                "category=excluded.category, confidence=excluded.confidence, "
                "cosine=excluded.cosine, source=excluded.source, "
                "classifier_rev=excluded.classifier_rev, "
                "updated_at=CURRENT_TIMESTAMP",
                params,
            )
        else:
            # MySQL has no ON CONFLICT; VALUES() keeps this readable on 8.0.
            _execute(
                conn,
                "INSERT INTO spend_categories "
                "(spend_id, category, confidence, cosine, source, classifier_rev) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON DUPLICATE KEY UPDATE "
                "category=VALUES(category), confidence=VALUES(confidence), "
                "cosine=VALUES(cosine), source=VALUES(source), "
                "classifier_rev=VALUES(classifier_rev), "
                "updated_at=CURRENT_TIMESTAMP",
                params,
            )
        conn.commit()
    finally:
        if close:
            conn.close()


def get_spend_category(spend_id, conn=None):
    close = conn is None
    if conn is None:
        conn = _connection()
    try:
        cur = _execute(
            conn,
            "SELECT spend_id, category, confidence, cosine, source "
            "FROM spend_categories WHERE spend_id = ?",
            (spend_id,),
        )
        row = cur.fetchone()
        cur.close()
        return _category_row(row)
    finally:
        if close:
            conn.close()


def get_categories(spend_ids, conn=None):
    """`{spend_id: category row}` for the given ids (one query, not N)."""
    ids = [int(i) for i in spend_ids]
    if not ids:
        return {}
    close = conn is None
    if conn is None:
        conn = _connection()
    try:
        placeholders = ",".join("?" for _ in ids)
        cur = _execute(
            conn,
            "SELECT spend_id, category, confidence, cosine, source "
            f"FROM spend_categories WHERE spend_id IN ({placeholders})",
            tuple(ids),
        )
        rows = cur.fetchall()
        cur.close()
        return {int(r["spend_id"]): _category_row(r) for r in rows}
    finally:
        if close:
            conn.close()


def list_spends_without_category(limit=2000, conn=None):
    """History rows that were never classified (backfill input)."""
    close = conn is None
    if conn is None:
        conn = _connection()
    try:
        cur = _execute(
            conn,
            "SELECT s.id, s.amount, s.comment, s.spent_at FROM spends s "
            "LEFT JOIN spend_categories c ON c.spend_id = s.id "
            "WHERE c.spend_id IS NULL "
            "ORDER BY s.spent_at DESC, s.id DESC LIMIT ?",
            (int(limit),),
        )
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]
    finally:
        if close:
            conn.close()



def list_manual_labels(conn=None):
    """Every spend whose category a human confirmed (`source='manual'`).

    This is the training set for the weekly dataset export: only labels the
    user picked are exported, never the classifier's own output — training on
    that just reinforces its mistakes. Empty comments are skipped: they carry
    no text to embed.
    """
    close = conn is None
    if conn is None:
        conn = _connection()
    try:
        cur = _execute(
            conn,
            "SELECT s.id, s.amount, s.comment, s.spent_at, c.category "
            "FROM spends s JOIN spend_categories c ON c.spend_id = s.id "
            "WHERE c.source = 'manual' AND TRIM(s.comment) <> '' "
            "ORDER BY s.spent_at ASC, s.id ASC",
        )
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]
    finally:
        if close:
            conn.close()
