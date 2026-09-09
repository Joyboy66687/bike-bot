"""SQLite schema and business calculations.

All functions accept an optional database path so tests can use an isolated
in-memory database without touching the production database.
"""
import datetime
import asyncio
import math
import sqlite3
from pathlib import Path
from config import DATABASE_PATH, TZ


async def db_execute(query_fn, *args):
    """Run a synchronous DB entry point off the asyncio event loop."""
    return await asyncio.to_thread(query_fn, *args)


def init_db(database_path=DATABASE_PATH):
    conn = sqlite3.connect(database_path)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("""CREATE TABLE IF NOT EXISTS clients (
        tg_id INTEGER PRIMARY KEY, name TEXT, lang TEXT DEFAULT 'ru',
        balance INTEGER DEFAULT 0)""")
    # Foreign keys are enforced for new installations. SQLite cannot add an FK
    # to an existing table with ALTER TABLE; production databases need the
    # documented CREATE new/INSERT/DROP/RENAME migration if this is required.
    cursor.execute("""CREATE TABLE IF NOT EXISTS rents (
        id INTEGER PRIMARY KEY AUTOINCREMENT, client_id INTEGER,
        client_name TEXT, amount INTEGER, return_date TEXT,
        total_month_amount INTEGER, total_days INTEGER,
        paid_days INTEGER DEFAULT 0, period_days INTEGER DEFAULT 7,
        due_amount INTEGER, current_week INTEGER DEFAULT 1,
        total_weeks INTEGER DEFAULT 4, is_notified INTEGER DEFAULT 0,
        prepayment_notified INTEGER DEFAULT 0, last_overdue_notified_at TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        FOREIGN KEY (client_id) REFERENCES clients(tg_id))""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS repair_debts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, debtor_name TEXT NOT NULL,
        telegram_id INTEGER, client_id INTEGER, description TEXT NOT NULL,
        total_amount INTEGER NOT NULL CHECK(total_amount > 0),
        paid_amount INTEGER NOT NULL DEFAULT 0 CHECK(paid_amount >= 0),
        created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
        FOREIGN KEY (client_id) REFERENCES clients(tg_id))""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS operations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, operation_key TEXT NOT NULL UNIQUE,
        operation_type TEXT NOT NULL, client_id INTEGER, amount INTEGER NOT NULL,
        description TEXT NOT NULL, created_at TEXT NOT NULL)""")
    for sql in (
        "ALTER TABLE clients ADD COLUMN balance INTEGER DEFAULT 0",
        "ALTER TABLE rents ADD COLUMN total_days INTEGER",
        "ALTER TABLE rents ADD COLUMN status TEXT NOT NULL DEFAULT 'active'",
        "ALTER TABLE rents ADD COLUMN paid_days INTEGER DEFAULT 0",
        "ALTER TABLE rents ADD COLUMN period_days INTEGER DEFAULT 7",
        "ALTER TABLE rents ADD COLUMN due_amount INTEGER",
        "ALTER TABLE rents ADD COLUMN prepayment_notified INTEGER DEFAULT 0",
        "ALTER TABLE rents ADD COLUMN last_overdue_notified_at TEXT",
        "ALTER TABLE repair_debts ADD COLUMN telegram_id INTEGER",
        "ALTER TABLE repair_debts ADD COLUMN client_id INTEGER",
    ):
        try:
            cursor.execute(sql)
        except sqlite3.OperationalError:
            pass
    cursor.execute("UPDATE rents SET total_days = total_weeks * 7 WHERE total_days IS NULL")
    cursor.execute("UPDATE rents SET due_amount = amount WHERE due_amount IS NULL")
    cursor.execute("UPDATE repair_debts SET client_id = telegram_id WHERE client_id IS NULL")
    cursor.execute("""UPDATE rents SET period_days = total_days,
        due_amount = (amount * total_days + 6) / 7
        WHERE status = 'active' AND paid_days = 0 AND total_days BETWEEN 1 AND 6""")
    conn.commit()
    return conn


def record_operation(cursor, operation_key, operation_type, client_id, amount, description):
    cursor.execute(
        "INSERT OR IGNORE INTO operations "
        "(operation_key, operation_type, client_id, amount, description, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (operation_key, operation_type, client_id, amount, description,
         datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")),
    )
    return cursor.rowcount == 1


def parse_date(value):
    return datetime.datetime.strptime(value, "%d.%m.%Y").date()


def charge_rent_period(cursor, rent: dict, source: str) -> dict | None:
    if rent.get("status", "active") != "active":
        return None
    due_amount = rent["due_amount"] or rent["amount"]
    # The period identity deliberately excludes source: auto/manual/deposit
    # must all be idempotent for the same rent and paid-day snapshot.
    operation_key = f"rent:{rent['id']}:{rent['paid_days']}:{rent['period_days']}"
    cursor.execute("SELECT balance FROM clients WHERE tg_id = ?", (rent["client_id"],))
    row = cursor.fetchone()
    balance = row[0] if row else 0
    if balance < due_amount:
        return None
    if not record_operation(cursor, operation_key, f"rent_payment_{source}",
                            rent["client_id"], due_amount,
                            f"Аренда №{rent['id']}, {rent['period_days']} дн."):
        return None
    new_balance = balance - due_amount
    cursor.execute("UPDATE clients SET balance = ? WHERE tg_id = ?",
                   (new_balance, rent["client_id"]))
    new_paid_days = rent["paid_days"] + rent["period_days"]
    if new_paid_days < rent["total_days"]:
        next_period_days = min(7, rent["total_days"] - new_paid_days)
        next_due_amount = math.ceil(rent["amount"] * next_period_days / 7)
        new_date = (parse_date(rent["return_date"]) +
                    datetime.timedelta(days=next_period_days)).strftime("%d.%m.%Y")
        cursor.execute(
            "UPDATE rents SET current_week = ?, paid_days = ?, period_days = ?, "
            "due_amount = ?, return_date = ?, is_notified = 0, "
            "prepayment_notified = 0, last_overdue_notified_at = NULL WHERE id = ?",
            (rent["current_week"] + 1, new_paid_days, next_period_days,
             next_due_amount, new_date, rent["id"]))
        next_date, completed = new_date, False
    else:
        new_paid_days = rent["total_days"]
        cursor.execute("UPDATE rents SET status = 'completed', paid_days = ?, "
                       "is_notified = 1 WHERE id = ?", (new_paid_days, rent["id"]))
        next_date, completed = None, True
    return {"completed": completed, "new_balance": new_balance,
            "new_paid_days": new_paid_days, "next_date": next_date,
            "charged": due_amount, "client_id": rent["client_id"],
            "client_name": rent["client_name"], "rent_id": rent["id"]}


def should_send_overdue_reminder(due_date, last_notified_at, today=None):
    today = today or datetime.datetime.now(TZ).date()
    if not last_notified_at:
        return True
    last_date = parse_date(last_notified_at)
    if last_date >= today:
        return False
    days = max(0, (today - due_date).days)
    return days in {1, 3, 7} or (days > 7 and (days - 7) % 7 == 0)
