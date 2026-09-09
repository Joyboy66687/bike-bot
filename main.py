import asyncio
import sqlite3
import datetime
import math
import os
import warnings
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import BaseFilter, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from groq import AsyncGroq
from scheduler import offsite_backup
from config import BACKUP_PASSPHRASE_PATH
from db import (
    charge_rent_period as db_charge_rent_period,
    should_send_overdue_reminder as db_should_send_overdue_reminder,
)
from handlers.common import register as register_common_handlers
from handlers.rent import register as register_rent_handlers
from handlers.repair import register as register_repair_handlers
from handlers.wallet import register as register_wallet_handlers
from handlers.admin import register as register_admin_handlers, register_cleanup
from handlers.ai_panel import register as register_ai_handlers
from keyboards import get_repair_debts_keyboard

# Игнорируем предупреждения от сторонних библиотек
warnings.filterwarnings("ignore", category=UserWarning)

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "debts.db"
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
CLEAR_DB_PIN = os.getenv("CLEAR_DB_PIN", "7777")
admin_ids_raw = os.getenv("ADMIN_IDS", "")
try:
    ADMIN_IDS = [int(value.strip()) for value in admin_ids_raw.split(",") if value.strip()]
except ValueError as error:
    raise RuntimeError("ADMIN_IDS должен содержать Telegram ID через запятую") from error

if not BOT_TOKEN or not GROQ_API_KEY or not ADMIN_IDS:
    raise RuntimeError("В .env должны быть заданы BOT_TOKEN, GROQ_API_KEY и ADMIN_IDS")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
dp = Dispatcher()
router = Router()
admin_router = Router()
TZ = ZoneInfo("Europe/Warsaw")


class IsAdmin(BaseFilter):
    async def __call__(self, event) -> bool:
        return event.from_user.id in ADMIN_IDS


admin_router.message.filter(IsAdmin(), F.chat.type == "private")
admin_router.callback_query.filter(IsAdmin(), F.message.chat.type == "private")
router.message.filter(F.chat.type == "private")


def escape_md(text: str) -> str:
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', str(text))

class RentStates(StatesGroup):
    waiting_for_client_name = State()
    waiting_for_name = State()
    waiting_for_amount = State()
    waiting_for_duration = State()
    waiting_for_extra_days = State()
    waiting_for_deposit_amount = State()
    waiting_for_ai_prompt = State()
    waiting_for_ai_debtor = State()
    waiting_for_clear_pin = State()
    waiting_for_clear_confirmation = State()
    waiting_for_repair_debtor_name = State()
    waiting_for_repair_debtor_id = State()
    waiting_for_repair_amount = State()
    waiting_for_repair_description = State()
    waiting_for_repair_payment = State()
    waiting_for_repair_writeoff_confirmation = State()
    waiting_for_rent_cancel_confirmation = State()

 
TEXTS = {
    "ru": {
        "welcome": "🚲 Привет, {name}!\nДобро пожаловать в прокат.\n\nПередай владельцу свой **ID**: `{id}`.\nЯ сам автоматически напомню тебе, когда придет время внести еженедельную оплату!",
        "invoice": "🚲 *Оформлен долгосрочный контракт!*\n\n👤 Имя: *{c_name}*\n📅 Общий срок: *{total_days} дней*\n💳 Еженедельный платеж: *{amount} zł*\n⏳ Ближайшая оплата: *{date}*\n\nБот будет автоматически напоминать вам об оплате каждые 7 дней.",
        "remind": "🔔 *Напоминание о еженедельной оплате!*\n\nСегодня ({date}) необходимо внести оплату за велосипед по вашему контракту.\n🚲 Неделя: *{week_num} из {total_weeks}*\n💰 К оплате: *{amount} zł*.",
        "pre_remind": "⏰ *Напоминание о предстоящей оплате!*\n\nЗавтра ({date}) будет списана оплата за велосипед.\n🚲 Период: *{period_days} дн.*\n💰 К списанию: *{amount} zł*.\n\nПожалуйста, заранее пополните кошелёк.",
        "thank_you": "🎉 *Спасибо!* Ваш еженедельный платеж на сумму *{amount} zł* успешно получен. Контракт продлен! 🚲",
        "wallet": "🎒 *Мой кошелёк*\n\n💰 Твой текущий баланс: *{balance} zł*\n\nДля пополнения баланса обратись к администратору проката."
    },
    "uk": {
        "welcome": "🚲 Привіт, {name}!\nЛаскаво просимо до прокату.\n\nПередай власнику свій **ID**: `{id}`.\nЯ сам автоматично нагадаю тобі, коли прийде час внести щотижневу оплату!",
        "invoice": "🚲 *Оформлено довгостроковий контракт!*\n\n👤 Ім'я: *{c_name}*\n📅 Загальний термін: *{total_days} днів*\n💳 Щотижневий платіж: *{amount} zł*\n⏳ Найближча оплата: *{date}*\n\nБот буде автоматично нагадувати вам про оплату кожні 7 днів.",
        "remind": "🔔 *Нагадування про щотижневу оплату!*\n\nСьогодні ({date}) необхідно внести оплату за велосипед за вашим контрактом.\n🚲 Тиждень: *{week_num} з {total_weeks}*\n💰 До сплати: *{amount} zł*.",
        "pre_remind": "⏰ *Нагадування про майбутню оплату!*\n\nЗавтра ({date}) буде списана оплата за велосипед.\n🚲 Період: *{period_days} дн.*\n💰 До списання: *{amount} zł*.\n\nБудь ласка, заздалегідь поповніть гаманець.",
        "thank_you": "🎉 *Дякуємо!* Ваш щотижневий платіж на суму *{amount} zł* успішно отримано. Контракт продовжено! 🚲",
        "wallet": "🎒 *Мій гаманець*\n\n💰 Твій поточний баланс: *{balance} zł*\n\nДля поповнення балансу звернися до адміністратора прокату."
    }
}

       

def get_admin_keyboard():
    keyboard = [
        [types.KeyboardButton(text="➕ Оформить гибкий контракт")],
        [types.KeyboardButton(text="📋 Список всех долгов")],
        [types.KeyboardButton(text="🛠 Долги за ремонт")],
        [types.KeyboardButton(text="💰 Пополнить баланс кошелька")],
    ]
    if GROQ_API_KEY:
        keyboard.append([
            types.KeyboardButton(text="🧠 ИИ-Помощник"),
            types.KeyboardButton(text="📊 Статистика доходов"),
        ])
    else:
        keyboard.append([types.KeyboardButton(text="📊 Статистика доходов")])
    keyboard.extend([
        [types.KeyboardButton(text="📜 История операций")],
        [types.KeyboardButton(text="❌ Очистить всю базу")],
        [types.KeyboardButton(text="⬅️ Назад в меню")],
    ])
    return types.ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True
    )

def get_ai_panel_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Анализ долгов и оплат", callback_data="ai_debt_analysis")
    builder.button(text="💡 Рекомендации по прокату", callback_data="ai_recommendations")
    builder.button(text="❓ Задать свой вопрос ИИ", callback_data="ai_custom_question")
    builder.button(text="⬅️ Выйти из ИИ панели", callback_data="ai_exit")
    builder.adjust(1)
    return builder.as_markup()

def init_db():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER,
            client_name TEXT,
            amount INTEGER,
            return_date TEXT,
            total_month_amount INTEGER,
            total_days INTEGER,
            paid_days INTEGER DEFAULT 0,
            period_days INTEGER DEFAULT 7,
            due_amount INTEGER,
            current_week INTEGER DEFAULT 1,
            total_weeks INTEGER DEFAULT 4,
            is_notified INTEGER DEFAULT 0,
            prepayment_notified INTEGER DEFAULT 0,
            last_overdue_notified_at TEXT,
            status TEXT NOT NULL DEFAULT 'active'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            tg_id INTEGER PRIMARY KEY, 
            name TEXT, 
            lang TEXT DEFAULT 'ru',
            balance INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS repair_debts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            debtor_name TEXT NOT NULL,
            telegram_id INTEGER,
            client_id INTEGER,
            description TEXT NOT NULL,
            total_amount INTEGER NOT NULL CHECK(total_amount > 0),
            paid_amount INTEGER NOT NULL DEFAULT 0 CHECK(paid_amount >= 0),
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_key TEXT NOT NULL UNIQUE,
            operation_type TEXT NOT NULL,
            client_id INTEGER,
            amount INTEGER NOT NULL,
            description TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    try:
        cursor.execute("ALTER TABLE clients ADD COLUMN balance INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE rents ADD COLUMN total_days INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE rents ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    except sqlite3.OperationalError:
        pass
    for column_sql in (
        "ALTER TABLE rents ADD COLUMN paid_days INTEGER DEFAULT 0",
        "ALTER TABLE rents ADD COLUMN period_days INTEGER DEFAULT 7",
        "ALTER TABLE rents ADD COLUMN due_amount INTEGER",
        "ALTER TABLE rents ADD COLUMN prepayment_notified INTEGER DEFAULT 0",
        "ALTER TABLE rents ADD COLUMN last_overdue_notified_at TEXT",
    ):
        try:
            cursor.execute(column_sql)
        except sqlite3.OperationalError:
            pass
    try:
        cursor.execute("ALTER TABLE repair_debts ADD COLUMN telegram_id INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE repair_debts ADD COLUMN client_id INTEGER")
    except sqlite3.OperationalError:
        pass
    cursor.execute("UPDATE rents SET total_days = total_weeks * 7 WHERE total_days IS NULL")
    cursor.execute("UPDATE rents SET due_amount = amount WHERE due_amount IS NULL")
    cursor.execute("UPDATE repair_debts SET client_id = telegram_id WHERE client_id IS NULL")
    cursor.execute(
        "UPDATE rents SET period_days = total_days, due_amount = (amount * total_days + 6) / 7 "
        "WHERE status = 'active' AND paid_days = 0 AND total_days BETWEEN 1 AND 6"
    )
    conn.commit()
    conn.close()

def record_operation(cursor, operation_key, operation_type, client_id, amount, description):
    cursor.execute(
        "INSERT OR IGNORE INTO operations "
        "(operation_key, operation_type, client_id, amount, description, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            operation_key,
            operation_type,
            client_id,
            amount,
            description,
            datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        )
    )
    return cursor.rowcount == 1


def charge_rent_period(cursor, rent: dict, source: str) -> dict | None:
    if rent.get("status", "active") != "active":
        return None
    due_amount = rent["due_amount"] or rent["amount"]
    # Source is intentionally not part of the key: auto/manual/deposit cannot
    # charge the same rent period twice. Existing SQLite tables are not
    # automatically rebuilt with foreign keys; see ORACLE_SETUP.txt.
    operation_key = f"rent:{rent['id']}:{rent['paid_days']}:{rent['period_days']}"
    cursor.execute("SELECT balance FROM clients WHERE tg_id = ?", (rent["client_id"],))
    balance_row = cursor.fetchone()
    balance = balance_row[0] if balance_row else 0
    if balance < due_amount:
        return None
    if not record_operation(
        cursor, operation_key, f"rent_payment_{source}", rent["client_id"],
        due_amount, f"Аренда №{rent['id']}, {rent['period_days']} дн."
    ):
        return None
    new_balance = balance - due_amount
    cursor.execute("UPDATE clients SET balance = ? WHERE tg_id = ?", (new_balance, rent["client_id"]))
    new_paid_days = rent["paid_days"] + rent["period_days"]
    if new_paid_days < rent["total_days"]:
        next_period_days = min(7, rent["total_days"] - new_paid_days)
        next_due_amount = math.ceil(rent["amount"] * next_period_days / 7)
        new_date = (
            parse_date(rent["return_date"]) + datetime.timedelta(days=next_period_days)
        ).strftime("%d.%m.%Y")
        cursor.execute(
            "UPDATE rents SET current_week = ?, paid_days = ?, period_days = ?, due_amount = ?, "
            "return_date = ?, is_notified = 0, prepayment_notified = 0, "
            "last_overdue_notified_at = NULL WHERE id = ?",
            (rent["current_week"] + 1, new_paid_days, next_period_days, next_due_amount, new_date, rent["id"]),
        )
        next_date, completed = new_date, False
    else:
        new_paid_days = rent["total_days"]
        cursor.execute(
            "UPDATE rents SET status = 'completed', paid_days = ?, is_notified = 1 WHERE id = ?",
            (new_paid_days, rent["id"]),
        )
        next_date, completed = None, True
    return {
        "completed": completed, "new_balance": new_balance, "new_paid_days": new_paid_days,
        "next_date": next_date, "charged": due_amount, "client_id": rent["client_id"],
        "client_name": rent["client_name"], "rent_id": rent["id"],
    }


def parse_date(value):
    return datetime.datetime.strptime(value, "%d.%m.%Y").date()


def should_send_overdue_reminder(due_date, last_notified_at, today=None):
    """Return whether an overdue payment needs a new notification today.

    The first alert is sent immediately. After that the cadence is day 1, 3,
    7 and then once per week, which keeps an unpaid contract visible without
    flooding the client or owner every scheduler minute.
    """
    today = today or datetime.datetime.now(TZ).date()
    if not last_notified_at:
        return True
    last_notified_date = parse_date(last_notified_at)
    if last_notified_date >= today:
        return False
    days_overdue = max(0, (today - due_date).days)
    return days_overdue in {1, 3, 7} or (days_overdue > 7 and (days_overdue - 7) % 7 == 0)

# Keep the legacy router registrations stable while using the extracted DB
# implementation as the single source of truth.
charge_rent_period = db_charge_rent_period
should_send_overdue_reminder = db_should_send_overdue_reminder

def backup_database():
    database_path = DATABASE_PATH
    backup_dir = BASE_DIR / "backups"
    backup_dir.mkdir(exist_ok=True)
    backup_path = backup_dir / f"debts_{datetime.datetime.now(TZ).date().isoformat()}.db"
    source = sqlite3.connect(database_path)
    target = sqlite3.connect(backup_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    backups = sorted(backup_dir.glob("debts_*.db"), key=lambda path: path.stat().st_mtime, reverse=True)
    for old_backup in backups[30:]:
        old_backup.unlink()

def get_ai_business_context():
    today = datetime.datetime.now(TZ).date()
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, client_name, client_id, due_amount, return_date, paid_days, total_days, status "
        "FROM rents ORDER BY id DESC"
    )
    rents = cursor.fetchall()
    cursor.execute("SELECT tg_id, name, balance FROM clients ORDER BY name")
    clients = cursor.fetchall()
    cursor.execute(
        "SELECT id, debtor_name, description, total_amount, paid_amount, status "
        "FROM repair_debts WHERE status = 'open' ORDER BY id DESC"
    )
    repair_debts = cursor.fetchall()
    conn.close()

    lines = [
        f"Дата отчёта: {today.strftime('%d.%m.%Y')}",
        f"Активных контрактов: {sum(1 for rent in rents if rent[7] == 'active')}",
        f"Всего клиентов: {len(clients)}",
        "",
        "КЛИЕНТЫ И КОШЕЛЬКИ:",
    ]
    for tg_id, name, balance in clients:
        lines.append(f"- {name or 'Без имени'} (Telegram ID {tg_id}): баланс {balance or 0} zł")

    lines.append("\nАРЕНДА:")
    for rent_id, client_name, client_id, due_amount, return_date, paid_days, total_days, status in rents:
        due_date = datetime.datetime.strptime(return_date, "%d.%m.%Y").date()
        overdue = "ПРОСРОЧЕН" if status == "active" and due_date <= today else "по графику"
        lines.append(
            f"- Контракт #{rent_id}, {escape_md(client_name)}, ID {client_id}, статус {status}, "
            f"следующее списание {return_date}, сумма {due_amount or 0} zł, "
            f"оплачено дней {paid_days}/{total_days}, {overdue}"
        )

    lines.append("\nОТКРЫТЫЕ ДОЛГИ ЗА РЕМОНТ:")
    if repair_debts:
        for debt_id, debtor_name, description, total_amount, paid_amount, status in repair_debts:
            lines.append(
                f"- Долг #{debt_id}, {escape_md(debtor_name)}, {escape_md(description)}: "
                f"остаток {total_amount - paid_amount} zł из {total_amount} zł"
            )
    else:
        lines.append("- Открытых долгов нет")
    return "\n".join(lines)

async def process_repair_wallet_payments(cursor):
    cursor.execute(
        "SELECT id, debtor_name, client_id, telegram_id, total_amount, paid_amount "
        "FROM repair_debts WHERE status = 'open' AND COALESCE(client_id, telegram_id) IS NOT NULL "
        "ORDER BY id ASC"
    )
    debts = cursor.fetchall()
    for debt_id, debtor_name, client_id, telegram_id, total_amount, paid_amount in debts:
        wallet_id = client_id or telegram_id
        cursor.execute("SELECT balance FROM clients WHERE tg_id = ?", (wallet_id,))
        client = cursor.fetchone()
        if not client:
            continue
        cursor.execute(
            "SELECT return_date FROM rents WHERE client_id = ? AND status = 'active'",
            (wallet_id,)
        )
        rent_due = any(
            datetime.datetime.strptime(row[0], "%d.%m.%Y").date() <= datetime.datetime.now(TZ).date()
            for row in cursor.fetchall()
        )
        if rent_due:
            continue
        balance = client[0] or 0
        remaining = total_amount - paid_amount
        payment = min(balance, remaining)
        if payment <= 0:
            continue

        operation_key = f"repair:{debt_id}:{paid_amount}:{payment}"
        if not record_operation(cursor, operation_key, "repair_payment_auto", wallet_id, payment, f"Ремонтный долг №{debt_id}"):
            continue
        new_balance = balance - payment
        new_paid = paid_amount + payment
        new_status = "paid" if new_paid >= total_amount else "open"
        cursor.execute("UPDATE clients SET balance = ? WHERE tg_id = ?", (new_balance, wallet_id))
        cursor.execute(
            "UPDATE repair_debts SET paid_amount = ?, status = ? WHERE id = ?",
            (new_paid, new_status, debt_id)
        )

        status_text = "✅ Долг полностью погашен." if new_status == "paid" else f"Остаток долга: *{total_amount - new_paid} zł*."
        if telegram_id:
            try:
                await bot.send_message(
                    chat_id=telegram_id,
                    text=f"💳 *Автоматическая оплата ремонта*\n\n"
                         f"🔧 Долг №{debt_id}\n💵 Списано с кошелька: *{payment} zł*\n"
                         f"{status_text}\n💰 Остаток кошелька: *{new_balance} zł*"
                )
            except Exception:
                logger.exception("Не удалось отправить сообщение")
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=f"🛠 *Автосписание долга за ремонт*\n\n"
                         f"👤 Клиент: *{escape_md(debtor_name)}*\n💵 Списано: *{payment} zł*\n"
                         f"💰 Остаток кошелька: *{new_balance} zł*\n{status_text}"
                )
            except Exception:
                logger.exception("Не удалось отправить сообщение")

async def check_deadlines():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    today = datetime.datetime.now(TZ).date()
    tomorrow = today + datetime.timedelta(days=1)

    cursor.execute(
        "SELECT id, client_id, client_name, due_amount, return_date, current_week, total_weeks, period_days "
        "FROM rents WHERE prepayment_notified = 0 AND status = 'active'"
    )
    upcoming_rents = [
        rent for rent in cursor.fetchall()
        if parse_date(rent[4]) == tomorrow
    ]

    for rent_id, client_id, client_name, due_amount, return_date, current_week, total_weeks, period_days in upcoming_rents:
        cursor.execute("SELECT lang, balance FROM clients WHERE tg_id = ?", (client_id,))
        client_res = cursor.fetchone()
        lang = client_res[0] if client_res else "ru"
        balance = client_res[1] if client_res else 0
        due_amount = due_amount or 0
        reminder_sent = False
        client_message = TEXTS[lang]["pre_remind"].format(
            date=return_date,
            period_days=period_days,
            amount=due_amount,
        )

        try:
            await bot.send_message(chat_id=client_id, text=client_message)
            reminder_sent = True
        except Exception:
            logger.exception("Не удалось отправить сообщение")

        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=(
                        "⏰ *Напоминание владельцу о завтрашнем списании*\n\n"
                        f"🆔 Контракт: *{rent_id}*\n"
                        f"👤 Клиент: *{escape_md(client_name)}*\n"
                        f"📅 Дата списания: *{return_date}*\n"
                        f"🚲 Период: *{period_days} дн.*\n"
                        f"💰 К списанию: *{due_amount} zł*\n"
                        f"🎒 Баланс клиента: *{balance} zł*"
                    ),
                )
                reminder_sent = True
            except Exception:
                logger.exception("Не удалось отправить сообщение")

        if reminder_sent:
            cursor.execute("UPDATE rents SET prepayment_notified = 1 WHERE id = ?", (rent_id,))
    
    cursor.execute(
        "SELECT id, client_id, client_name, amount, due_amount, return_date, current_week, total_weeks, paid_days, period_days, total_days, last_overdue_notified_at "
        "FROM rents WHERE status = 'active'"
    )
    active_rents = [
        rent for rent in cursor.fetchall()
        if parse_date(rent[5]) <= today
    ]
    
    for rent in active_rents:
        rent_id, client_id, client_name, weekly_amount, due_amount, return_date, current_week, total_weeks, paid_days, period_days, total_days, last_overdue_notified_at = rent
        due_amount = due_amount or weekly_amount
        
        cursor.execute("SELECT lang, balance FROM clients WHERE tg_id = ?", (client_id,))
        client_res = cursor.fetchone()
        lang = client_res[0] if client_res else "ru"
        balance = client_res[1] if client_res else 0
        
        rent_info = {
            "id": rent_id, "client_id": client_id, "client_name": client_name,
            "amount": weekly_amount, "due_amount": due_amount, "return_date": return_date,
            "current_week": current_week, "total_weeks": total_weeks, "paid_days": paid_days,
            "period_days": period_days, "total_days": total_days,
        }
        if balance >= due_amount:
            result = charge_rent_period(cursor, rent_info, "auto")
            if result is None:
                continue
            new_balance = result["new_balance"]
            new_paid_days = result["new_paid_days"]
            new_date = result["next_date"]
            if not result["completed"]:
                try:
                    msg_client = (
                        f"💳 *Автоматическая оплата аренды!*\n\nС кошелька списано: *{due_amount} zł* за {period_days} дн.\n🚲 Оплачено дней: *{new_paid_days} из {total_days}*\n📅 Следующий платеж: *{new_date}*\n💰 Остаток: *{new_balance} zł*"
                        if lang == "ru" else
                        f"💳 *Автоматична оплата оренди!*\n\nЗ гаманця списано: *{due_amount} zł* за {period_days} дн.\n🚲 Оплачено днів: *{new_paid_days} з {total_days}*\n📅 Наступний платіж: *{new_date}*\n💰 Залишок: *{new_balance} zł*"
                    )
                    await bot.send_message(chat_id=client_id, text=msg_client)
                except Exception:
                    logger.exception("Не удалось отправить сообщение")
                
                for admin_id in ADMIN_IDS:
                    try:
                        await bot.send_message(chat_id=admin_id, text=f"✅ *Автооплата по кошельку!*\n\n🆔 *Контракт:* {rent_id}\n👤 *Клиент:* {escape_md(client_name)}\n🚲 *Оплачено дней:* {new_paid_days} из {total_days}\n💵 *Списано:* {due_amount} zł\n💰 *Остаток у клиента:* {new_balance} zł")
                    except Exception:
                        logger.exception("Не удалось отправить сообщение")
            else:
                try:
                    await bot.send_message(chat_id=client_id, text=TEXTS[lang]["thank_you"].format(amount=due_amount) + "\n🎉 Контракт успешно завершен!")
                except Exception:
                    logger.exception("Не удалось отправить сообщение")
                
                for admin_id in ADMIN_IDS:
                    try:
                        await bot.send_message(chat_id=admin_id, text=f"🎉 *Контракт №{rent_id} полностью закрыт!*\n👤 *Клиент:* {escape_md(client_name)}\nВся сумма за весь срок успешно выплачена через кошелёк.")
                    except Exception:
                        logger.exception("Не удалось отправить сообщение")
        else:
            due_date = parse_date(return_date)
            if not should_send_overdue_reminder(due_date, last_overdue_notified_at, today):
                continue

            days_overdue = max(0, (today - due_date).days)
            overdue_note = (
                "Сегодня дата списания."
                if days_overdue == 0
                else f"Платёж просрочен на {days_overdue} дн."
            )
            notification_sent = False
            try:
                await bot.send_message(
                    chat_id=client_id,
                    text=(
                        TEXTS[lang]["remind"].format(
                            date=return_date,
                            week_num=current_week,
                            total_weeks=total_weeks,
                            amount=due_amount,
                        )
                        + f"\n\n⚠️ _{overdue_note} На вашем кошельке недостаточно средств ({balance} zł). "
                        "Пожалуйста, пополните баланс через администратора!_"
                    ),
                )
                notification_sent = True
            except Exception:
                logger.warning("Не удалось отправить напоминание клиенту %s", client_id)

            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=(
                            "⚠️ *Долг! Недостаточно средств на кошельке!*\n\n"
                            f"🆔 *Контракт:* {rent_id}\n👤 *Клиент:* {escape_md(client_name)}\n"
                            f"⏱ *Статус:* {overdue_note}\n🚲 *Период:* {period_days} дн.\n"
                            f"💰 *Требуется:* {due_amount} zł\n🎒 *Баланс кошелька:* {balance} zł"
                        ),
                    )
                    notification_sent = True
                except Exception:
                    logger.warning("Не удалось отправить напоминание администратору %s", admin_id)

            if notification_sent:
                cursor.execute(
                    "UPDATE rents SET is_notified = 1, last_overdue_notified_at = ? WHERE id = ?",
                    (today.strftime("%d.%m.%Y"), rent_id),
                )

    await process_repair_wallet_payments(cursor)
            
    conn.commit()
    conn.close()

async def main():
    init_db()
    backup_database()
    register_common_handlers(
        router,
        admin_router,
        SimpleNamespace(
            TEXTS=TEXTS, ADMIN_IDS=ADMIN_IDS, DATABASE_PATH=DATABASE_PATH,
            bot=bot, escape_md=escape_md, get_admin_keyboard=get_admin_keyboard,
            RentStates=RentStates, sqlite3=sqlite3,
        ),
    )
    register_rent_handlers(
        router,
        admin_router,
        SimpleNamespace(
            math=math, datetime=datetime, sqlite3=sqlite3,
            DATABASE_PATH=DATABASE_PATH, bot=bot, ADMIN_IDS=ADMIN_IDS,
            TEXTS=TEXTS, TZ=TZ, logger=logger, escape_md=escape_md,
            RentStates=RentStates, check_deadlines=check_deadlines,
            charge_rent_period=charge_rent_period, parse_date=parse_date,
            should_send_overdue_reminder=should_send_overdue_reminder,
            get_repair_debts_keyboard=get_repair_debts_keyboard,
            get_admin_keyboard=get_admin_keyboard,
        ),
    )
    register_repair_handlers(
        router,
        admin_router,
        SimpleNamespace(
            sqlite3=sqlite3, DATABASE_PATH=DATABASE_PATH, bot=bot,
            ADMIN_IDS=ADMIN_IDS, logger=logger, escape_md=escape_md,
            RentStates=RentStates, get_admin_keyboard=get_admin_keyboard,
            check_deadlines=check_deadlines, record_operation=record_operation,
        ),
    )
    register_wallet_handlers(
        router,
        admin_router,
        SimpleNamespace(
            math=math, datetime=datetime, sqlite3=sqlite3,
            DATABASE_PATH=DATABASE_PATH, bot=bot, ADMIN_IDS=ADMIN_IDS,
            TEXTS=TEXTS, TZ=TZ, logger=logger, escape_md=escape_md,
            RentStates=RentStates, get_admin_keyboard=get_admin_keyboard,
            check_deadlines=check_deadlines,
            charge_rent_period=charge_rent_period,
            record_operation=record_operation,
        ),
    )
    register_admin_handlers(
        router,
        admin_router,
        SimpleNamespace(
            sqlite3=sqlite3, DATABASE_PATH=DATABASE_PATH, escape_md=escape_md,
        ),
    )
    register_cleanup(
        admin_router,
        SimpleNamespace(
            sqlite3=sqlite3, DATABASE_PATH=DATABASE_PATH,
            RentStates=RentStates, CLEAR_DB_PIN=CLEAR_DB_PIN,
            get_admin_keyboard=get_admin_keyboard,
        ),
    )
    register_ai_handlers(
        router,
        admin_router,
        SimpleNamespace(
            bot=bot, logger=logger, GROQ_API_KEY=GROQ_API_KEY,
            GROQ_MODEL=GROQ_MODEL, RentStates=RentStates,
            get_ai_panel_keyboard=get_ai_panel_keyboard,
            get_admin_keyboard=get_admin_keyboard,
            get_ai_business_context=get_ai_business_context,
        ),
    )
    dp.include_router(admin_router)
    dp.include_router(router)
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_deadlines, "interval", minutes=1)
    scheduler.add_job(backup_database, "interval", hours=24)
    scheduler.add_job(
        offsite_backup, "interval", hours=24,
        args=[bot, ADMIN_IDS, BASE_DIR / "backups", BACKUP_PASSPHRASE_PATH],
    )
    scheduler.start()
    print("Bot started successfully!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
