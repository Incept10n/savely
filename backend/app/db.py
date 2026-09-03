import os
import urllib.parse
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
        conn.commit()
        return affected > 0
    finally:
        if close:
            conn.close()


def init_db():
    conn = _connection()
    try:
        _execute(conn, MYSQL_SCHEMA if not _is_sqlite() else SCHEMA)
        _execute(
            conn,
            MYSQL_AI_USAGE_SCHEMA if not _is_sqlite() else AI_USAGE_SCHEMA,
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


def _build_database_url():
    user = urllib.parse.quote(current_app.config["DB_USER"])
    password = urllib.parse.quote(current_app.config["DB_PASSWORD"])
    host = current_app.config["DB_HOST"]
    port = current_app.config["DB_PORT"]
    name = current_app.config["DB_NAME"]
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}"
