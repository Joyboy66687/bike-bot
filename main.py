import asyncio
import sqlite3
import datetime
import math
import os
import warnings
import logging
from pathlib import Path
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from groq import AsyncGroq

# Игнорируем предупреждения от сторонних библиотек
warnings.filterwarnings("ignore", category=UserWarning)

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "debts.db"
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = "openai/gpt-oss-20b"
admin_ids_raw = os.getenv("ADMIN_IDS", "")
try:
    ADMIN_IDS = [int(value.strip()) for value in admin_ids_raw.split(",") if value.strip()]
except ValueError as error:
    raise RuntimeError("ADMIN_IDS должен содержать Telegram ID через запятую") from error

if not BOT_TOKEN or not ADMIN_IDS:
    raise RuntimeError("В .env должны быть заданы BOT_TOKEN и ADMIN_IDS")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
dp = Dispatcher()
router = Router()
dp.include_router(router)

class RentStates(StatesGroup):
    waiting_for_client_name = State()
    waiting_for_name = State()
    waiting_for_amount = State()
    waiting_for_duration = State()
    waiting_for_extra_days = State()
    waiting_for_deposit_amount = State()
    waiting_for_ai_prompt = State()
    waiting_for_ai_debtor = State()
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


def parse_date(value):
    return datetime.datetime.strptime(value, "%d.%m.%Y").date()


def should_send_overdue_reminder(due_date, last_notified_at, today=None):
    """Return whether an overdue payment needs a new notification today.

    The first alert is sent immediately. After that the cadence is day 1, 3,
    7 and then once per week, which keeps an unpaid contract visible without
    flooding the client or owner every scheduler minute.
    """
    today = today or datetime.date.today()
    if not last_notified_at:
        return True
    last_notified_date = parse_date(last_notified_at)
    if last_notified_date >= today:
        return False
    days_overdue = max(0, (today - due_date).days)
    return days_overdue in {1, 3, 7} or (days_overdue > 7 and (days_overdue - 7) % 7 == 0)

def backup_database():
    database_path = DATABASE_PATH
    backup_dir = BASE_DIR / "backups"
    backup_dir.mkdir(exist_ok=True)
    backup_path = backup_dir / f"debts_{datetime.date.today().isoformat()}.db"
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
    today = datetime.date.today()
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
            f"- Контракт #{rent_id}, {client_name}, ID {client_id}, статус {status}, "
            f"следующее списание {return_date}, сумма {due_amount or 0} zł, "
            f"оплачено дней {paid_days}/{total_days}, {overdue}"
        )

    lines.append("\nОТКРЫТЫЕ ДОЛГИ ЗА РЕМОНТ:")
    if repair_debts:
        for debt_id, debtor_name, description, total_amount, paid_amount, status in repair_debts:
            lines.append(
                f"- Долг #{debt_id}, {debtor_name}, {description}: "
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
            datetime.datetime.strptime(row[0], "%d.%m.%Y").date() <= datetime.date.today()
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
                pass
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=f"🛠 *Автосписание долга за ремонт*\n\n"
                         f"👤 Клиент: *{debtor_name}*\n💵 Списано: *{payment} zł*\n"
                         f"💰 Остаток кошелька: *{new_balance} zł*\n{status_text}"
                )
            except Exception:
                pass

async def check_deadlines():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    today = datetime.date.today()
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
            pass

        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=(
                        "⏰ *Напоминание владельцу о завтрашнем списании*\n\n"
                        f"🆔 Контракт: *{rent_id}*\n"
                        f"👤 Клиент: *{client_name}*\n"
                        f"📅 Дата списания: *{return_date}*\n"
                        f"🚲 Период: *{period_days} дн.*\n"
                        f"💰 К списанию: *{due_amount} zł*\n"
                        f"🎒 Баланс клиента: *{balance} zł*"
                    ),
                )
                reminder_sent = True
            except Exception:
                pass

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
        
        if balance >= due_amount:
            operation_key = f"rent:{rent_id}:{paid_days}:{period_days}"
            if not record_operation(cursor, operation_key, "rent_payment_auto", client_id, due_amount, f"Аренда №{rent_id}, {period_days} дн."):
                continue
            new_balance = balance - due_amount
            cursor.execute("UPDATE clients SET balance = ? WHERE tg_id = ?", (new_balance, client_id))
            new_paid_days = paid_days + period_days
            
            if new_paid_days < total_days:
                next_week = current_week + 1
                old_date = parse_date(return_date)
                next_period_days = min(7, total_days - new_paid_days)
                next_due_amount = math.ceil(weekly_amount * next_period_days / 7)
                new_date = (old_date + datetime.timedelta(days=next_period_days)).strftime("%d.%m.%Y")
                
                cursor.execute(
                    "UPDATE rents SET current_week = ?, paid_days = ?, period_days = ?, due_amount = ?, "
                    "return_date = ?, is_notified = 0, prepayment_notified = 0, "
                    "last_overdue_notified_at = NULL WHERE id = ?",
                    (next_week, new_paid_days, next_period_days, next_due_amount, new_date, rent_id),
                )
                
                try:
                    msg_client = (
                        f"💳 *Автоматическая оплата аренды!*\n\nС кошелька списано: *{due_amount} zł* за {period_days} дн.\n🚲 Оплачено дней: *{new_paid_days} из {total_days}*\n📅 Следующий платеж: *{new_date}*\n💰 Остаток: *{new_balance} zł*"
                        if lang == "ru" else
                        f"💳 *Автоматична оплата оренди!*\n\nЗ гаманця списано: *{due_amount} zł* за {period_days} дн.\n🚲 Оплачено днів: *{new_paid_days} з {total_days}*\n📅 Наступний платіж: *{new_date}*\n💰 Залишок: *{new_balance} zł*"
                    )
                    await bot.send_message(chat_id=client_id, text=msg_client)
                except: pass
                
                for admin_id in ADMIN_IDS:
                    try:
                        await bot.send_message(chat_id=admin_id, text=f"✅ *Автооплата по кошельку!*\n\n🆔 *Контракт:* {rent_id}\n👤 *Клиент:* {client_name}\n🚲 *Оплачено дней:* {new_paid_days} из {total_days}\n💵 *Списано:* {due_amount} zł\n💰 *Остаток у клиента:* {new_balance} zł")
                    except: pass
            else:
                cursor.execute("UPDATE rents SET status = 'completed', is_notified = 1 WHERE id = ?", (rent_id,))
                try:
                    await bot.send_message(chat_id=client_id, text=TEXTS[lang]["thank_you"].format(amount=due_amount) + "\n🎉 Контракт успешно завершен!")
                except: pass
                
                for admin_id in ADMIN_IDS:
                    try:
                        await bot.send_message(chat_id=admin_id, text=f"🎉 *Контракт №{rent_id} полностью закрыт!*\n👤 *Клиент:* {client_name}\nВся сумма за весь срок успешно выплачена через кошелёк.")
                    except: pass
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
                            f"🆔 *Контракт:* {rent_id}\n👤 *Клиент:* {client_name}\n"
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

@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id in ADMIN_IDS:
        await message.answer("👑 Привет, Владелец! Используй меню:", reply_markup=get_admin_keyboard())
        return
        
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT lang FROM clients WHERE tg_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    
    if res:
        lang = res[0]
        client_menu = types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="🎒 Мой кошелёк / Мій гаманець")]], resize_keyboard=True)
        await message.answer(TEXTS[lang]["welcome"].format(name=message.from_user.full_name, id=user_id), reply_markup=client_menu)
    else:
        builder = InlineKeyboardBuilder()
        builder.button(text="🇷🇺 Русский", callback_data="setlang_ru")
        builder.button(text="🇺🇦 Українська", callback_data="setlang_uk")
        await message.answer("🚲 Выберите язык интерфейса / Оберіть мову інтерфейсу:", reply_markup=builder.as_markup())

@router.message(F.text == "⬅️ Назад в меню")
async def back_to_admin_menu(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await message.answer(
        "↩️ Текущий сценарий отменён. Главное меню:",
        reply_markup=get_admin_keyboard()
    )

@router.callback_query(F.data.startswith("setlang_"))
async def process_set_lang(callback: types.CallbackQuery):
    lang = callback.data.split("_")[1]
    if lang not in TEXTS:
        await callback.answer("Неизвестный язык", show_alert=True)
        return
    user_id = callback.from_user.id
    user_name = callback.from_user.full_name
    if user_id in ADMIN_IDS:
        await callback.message.answer("👑 Вы зарегистрированы как владелец.", reply_markup=get_admin_keyboard())
        await callback.answer()
        return
    
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO clients (tg_id, name, lang, balance) VALUES (?, ?, ?, COALESCE((SELECT balance FROM clients WHERE tg_id = ?), 0))", (user_id, user_name, lang, user_id))
    conn.commit()
    conn.close()
    
    welcome_msg = TEXTS[lang]["welcome"].format(name=user_name, id=user_id)
    client_menu = types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="🎒 Мой кошелёк / Мій гаманець")]], resize_keyboard=True)
    await callback.message.answer(welcome_msg, reply_markup=client_menu)
    await callback.answer()

@router.message(F.text == "🎒 Мой кошелёк / Мій гаманець")
async def text_open_wallet(message: types.Message):
    user_id = message.from_user.id
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT lang, balance FROM clients WHERE tg_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    
    lang = res[0] if res else "ru"
    balance = res[1] if res else 0
    await message.answer(TEXTS[lang]["wallet"].format(balance=balance))

@router.message(F.text == "➕ Оформить гибкий контракт")
async def start_rent(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await message.answer("👤 Шаг 1: Введите **имя клиента**:")
    await state.set_state(RentStates.waiting_for_client_name)

@router.message(RentStates.waiting_for_client_name)
async def process_client_name(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    client_name = message.text.strip()
    if not client_name:
        await message.answer("❌ Имя не может быть пустым. Введите имя клиента:")
        return
    await state.update_data(client_name=client_name)
    await message.answer("🆔 Шаг 2: Введите **Telegram ID** клиента:")
    await state.set_state(RentStates.waiting_for_name)

@router.message(RentStates.waiting_for_name)
async def process_client_id(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    if not message.text or not message.text.isdigit() or int(message.text) <= 0:
        await message.answer("❌ Введите положительный числовой Telegram ID клиента.")
        return
    await state.update_data(c_id=int(message.text))
    await message.answer("💰 Шаг 3: Введите **цену за 1 неделю** аренды (в zł):")
    await state.set_state(RentStates.waiting_for_amount)

@router.message(RentStates.waiting_for_amount)
async def process_amount(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    if not message.text or not message.text.isdigit() or int(message.text) <= 0:
        await message.answer("❌ Введите положительный тариф за неделю целым числом.")
        return
    await state.update_data(amount=int(message.text))
    await message.answer("⏳ Шаг 4: На сколько **ДНЕЙ** оформляется аренда?\n*(Например: 90 дней):*")
    await state.set_state(RentStates.waiting_for_duration)

@router.message(RentStates.waiting_for_duration)
async def process_duration(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    if not message.text or not message.text.isdigit():
        await message.answer("❌ Введите срок аренды положительным целым числом дней.")
        return
    total_days = int(message.text)
    if total_days <= 0:
        await message.answer("❌ Срок аренды должен быть хотя бы 1 день.")
        return
    weeks = max(1, math.ceil(total_days / 7))
    data = await state.get_data()
    weekly_amount = data['amount']
    period_days = min(7, total_days)
    due_amount = math.ceil(weekly_amount * period_days / 7)
    client_id = data['c_id']
    total_month_amount = math.ceil(weekly_amount * total_days / 7)
    
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT name, lang FROM clients WHERE tg_id = ?", (client_id,))
    c_res = cursor.fetchone()
    c_name = data.get("client_name") or (c_res[0] if c_res else f"ID: {client_id}")
    lang = c_res[1] if c_res else "ru"
    
    start_date = datetime.date.today()
    return_date = (start_date + datetime.timedelta(days=period_days)).strftime("%d.%m.%Y")
    
    cursor.execute("""
        INSERT INTO rents (client_id, client_name, amount, return_date, total_month_amount, total_days, paid_days, period_days, due_amount, current_week, total_weeks, is_notified, status)
        VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, 1, ?, 0, 'active')
    """, (client_id, c_name, weekly_amount, return_date, total_month_amount, total_days, period_days, due_amount, weeks))
    conn.commit()
    conn.close()
    
    await message.answer(f"✅ Контракт успешно создан для *{c_name}*!\nДедлайн первой недели: {return_date}", reply_markup=get_admin_keyboard())
    
    try:
        await bot.send_message(chat_id=client_id, text=TEXTS[lang]["invoice"].format(c_name=c_name, total_days=total_days, amount=weekly_amount, date=return_date))
    except: pass
    await state.clear()

@router.message(F.text == "📋 Список всех долгов")
async def list_debts(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, client_name, amount, due_amount, return_date, total_days, paid_days, current_week, total_weeks, client_id, status FROM rents")
    rents = cursor.fetchall()
    conn.close()
    
    if not rents:
        await message.answer("📋 История контрактов пока пуста.")
        return
        
    for r in rents:
        r_id, c_name, amount, due_amount, r_date, total_days, paid_days, cur_w, tot_w, c_id, status = r
        builder = InlineKeyboardBuilder()
        if status == "active":
            builder.button(text="💳 Оплачена неделя", callback_data=f"close_{r_id}")
            status_text = "🔄 Активен"
        else:
            status_text = "✅ Контракт полностью оплачен"
        builder.button(text="📅 Продлить срок", callback_data=f"extend_{r_id}")
        builder.button(text="🗑 Удалить контракт", callback_data=f"delete_rent_{r_id}")
        
        total_contract_amount = math.ceil(amount * total_days / 7)
        text = f"🆔 *Контракт №{r_id}*\n👤 Клиент: *{c_name}* (ID: `{c_id}`)\n📌 Статус: *{status_text}*\n💳 Тариф: *{amount} zł/неделя*\n📅 Срок: *{total_days} дней* ({tot_w} периодов)\n💰 Общая сумма: *{total_contract_amount} zł*\n🚲 Оплачено дней: *{paid_days} из {total_days}*\n💵 Ближайшее списание: *{due_amount or amount} zł*\n⏳ Срок платежа: *{r_date}*"
        await message.answer(text, reply_markup=builder.as_markup())

def get_repair_debts_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Записать новый долг", callback_data="repair_add")
    builder.button(text="📋 Обновить список", callback_data="repair_list")
    builder.button(text="⬅️ В главное меню", callback_data="repair_exit")
    builder.adjust(1)
    return builder.as_markup()

@router.message(F.text == "🛠 Долги за ремонт")
async def open_repair_debts_panel(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer(
        "🛠 *Панель долгов за ремонт*\n\n"
        "Здесь можно записать ремонт в долг, видеть остаток и отмечать частичные платежи.",
        reply_markup=get_repair_debts_keyboard()
    )

@router.callback_query(F.data == "repair_add")
async def start_repair_debt(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await callback.message.answer("👤 Введите имя клиента или короткое описание, например: *Иван Петров*")
    await state.set_state(RentStates.waiting_for_repair_debtor_name)
    await callback.answer()

@router.message(RentStates.waiting_for_repair_debtor_name)
async def process_repair_debtor_name(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    name = message.text.strip()
    if not name:
        await message.answer("❌ Имя не может быть пустым.")
        return
    await state.update_data(repair_debtor_name=name)
    await message.answer("🆔 Введите Telegram ID клиента для уведомлений или `0`, если уведомлять только владельца:")
    await state.set_state(RentStates.waiting_for_repair_debtor_id)

@router.message(RentStates.waiting_for_repair_debtor_id)
async def process_repair_debtor_id(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    if not message.text.isdigit():
        await message.answer("❌ Введите числовой Telegram ID или `0`.")
        return
    telegram_id = int(message.text)
    await state.update_data(repair_telegram_id=telegram_id or None)
    await message.answer("💰 Введите полную стоимость ремонта в zł, например: *700*")
    await state.set_state(RentStates.waiting_for_repair_amount)

@router.message(RentStates.waiting_for_repair_amount)
async def process_repair_amount(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    if not message.text.isdigit() or int(message.text) <= 0:
        await message.answer("❌ Введите положительную сумму целым числом.")
        return
    await state.update_data(repair_total_amount=int(message.text))
    await message.answer("🔧 Что ремонтировали? Например: *Замена камеры и настройка тормозов*")
    await state.set_state(RentStates.waiting_for_repair_description)

@router.message(RentStates.waiting_for_repair_description)
async def process_repair_description(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    description = message.text.strip()
    if not description:
        await message.answer("❌ Описание ремонта не может быть пустым.")
        return
    data = await state.get_data()
    created_at = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO repair_debts (debtor_name, telegram_id, client_id, description, total_amount, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (data["repair_debtor_name"], data.get("repair_telegram_id"), data.get("repair_telegram_id"), description, data["repair_total_amount"], created_at)
    )
    debt_id = cursor.lastrowid
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer(
        f"✅ *Долг за ремонт №{debt_id} записан!*\n\n"
        f"👤 Клиент: *{data['repair_debtor_name']}*\n"
        f"🔧 Работа: {description}\n"
        f"💰 Сумма: *{data['repair_total_amount']} zł*\n"
        f"📅 Создан: {created_at}",
        reply_markup=get_repair_debts_keyboard()
    )
    notification = (
        f"🛠 *У вас новый долг за ремонт №{debt_id}*\n\n"
        f"🔧 Работа: {description}\n💰 К оплате: *{data['repair_total_amount']} zł*\n"
        f"📅 Дата: {created_at}"
    )
    if data.get("repair_telegram_id"):
        try:
            await bot.send_message(chat_id=data["repair_telegram_id"], text=notification)
        except Exception:
            pass
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=f"🛠 *Записан долг за ремонт №{debt_id}*\n👤 Клиент: *{data['repair_debtor_name']}*\n💰 Сумма: *{data['repair_total_amount']} zł*"
            )
        except Exception:
            pass
    await check_deadlines()

async def send_repair_debts_list(target: types.Message | types.CallbackQuery):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, debtor_name, telegram_id, description, total_amount, paid_amount, created_at "
        "FROM repair_debts WHERE status = 'open' ORDER BY id DESC"
    )
    debts = cursor.fetchall()
    conn.close()

    if not debts:
        text = "📋 *Долги за ремонт*\n\n✅ Открытых долгов нет."
        keyboard = get_repair_debts_keyboard()
    else:
        total_remaining = sum(total - paid for _, _, _, _, total, paid, _ in debts)
        text = f"📋 *Долги за ремонт*\n💰 Общий остаток: *{total_remaining} zł*\n\n"
        keyboard_builder = InlineKeyboardBuilder()
        for debt_id, name, telegram_id, description, total, paid, created_at in debts:
            remaining = total - paid
            text += (
                f"🆔 *№{debt_id}* — *{name}*\n"
                f"🔧 {description}\n"
                f"💳 Остаток: *{remaining} zł* из {total} zł | оплачено {paid} zł\n"
                f"📅 {created_at}\n\n"
            )
            keyboard_builder.button(text=f"💵 Платёж по №{debt_id}", callback_data=f"repair_pay_{debt_id}")
            keyboard_builder.button(text=f"🗑 Закрыть долг №{debt_id}", callback_data=f"repair_delete_{debt_id}")
        keyboard_builder.button(text="🔄 Обновить", callback_data="repair_list")
        keyboard_builder.button(text="⬅️ В главное меню", callback_data="repair_exit")
        keyboard_builder.adjust(2, 1, 1)
        keyboard = keyboard_builder.as_markup()

    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=keyboard)
        await target.answer()
    else:
        await target.answer(text, reply_markup=keyboard)

@router.callback_query(F.data == "repair_list")
async def repair_debts_list_callback(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await send_repair_debts_list(callback)

@router.callback_query(F.data.startswith("repair_pay_"))
async def start_repair_payment(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    debt_id = int(callback.data.rsplit("_", 1)[1])
    await state.update_data(repair_debt_id=debt_id)
    await callback.message.answer(f"💵 Введите сумму платежа по долгу №{debt_id} в zł:")
    await state.set_state(RentStates.waiting_for_repair_payment)
    await callback.answer()

@router.message(RentStates.waiting_for_repair_payment)
async def process_repair_payment(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    if not message.text.isdigit() or int(message.text) <= 0:
        await message.answer("❌ Введите положительную сумму целым числом.")
        return

    data = await state.get_data()
    payment = int(message.text)
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT debtor_name, telegram_id, total_amount, paid_amount FROM repair_debts WHERE id = ? AND status = 'open'",
        (data["repair_debt_id"],)
    )
    debt = cursor.fetchone()
    if not debt:
        conn.close()
        await state.clear()
        await message.answer("❌ Открытый долг не найден.", reply_markup=get_repair_debts_keyboard())
        return

    name, telegram_id, total_amount, paid_amount = debt
    remaining = total_amount - paid_amount
    if payment > remaining:
        conn.close()
        await message.answer(f"❌ Платёж больше остатка. Максимум: *{remaining} zł*.")
        return

    new_paid = paid_amount + payment
    new_status = "paid" if new_paid == total_amount else "open"
    if not record_operation(
        cursor,
        f"repair:{data['repair_debt_id']}:{paid_amount}:{payment}:manual",
        "repair_payment_manual",
        telegram_id,
        payment,
        f"Ручная оплата ремонта №{data['repair_debt_id']}"
    ):
        conn.close()
        await state.clear()
        await message.answer("Этот платёж уже обработан.", reply_markup=get_repair_debts_keyboard())
        return
    cursor.execute(
        "UPDATE repair_debts SET paid_amount = ?, status = ? WHERE id = ?",
        (new_paid, new_status, data["repair_debt_id"])
    )
    conn.commit()
    conn.close()
    await state.clear()
    status_text = "✅ Долг полностью погашен!" if new_status == "paid" else f"Остаток: *{total_amount - new_paid} zł*."
    await message.answer(
        f"💳 Платёж *{payment} zł* записан для *{name}*.\n{status_text}",
        reply_markup=get_repair_debts_keyboard()
    )
    if telegram_id:
        try:
            await bot.send_message(
                chat_id=telegram_id,
                text=f"💳 *Платёж по долгу за ремонт получен: {payment} zł*\n"
                     f"👤 Клиент: {name}\n"
                     f"💰 Остаток: *{total_amount - new_paid} zł*"
            )
        except Exception:
            pass
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=f"✅ *Оплата ремонта записана*\n👤 Клиент: *{name}*\n💵 Получено: *{payment} zł*\n"
                     f"💰 Остаток: *{total_amount - new_paid} zł*"
            )
        except Exception:
            pass

@router.callback_query(F.data.startswith("repair_delete_"))
async def delete_repair_debt(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    debt_id = int(callback.data.rsplit("_", 1)[1])
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT debtor_name, telegram_id, total_amount, paid_amount FROM repair_debts WHERE id = ?", (debt_id,))
    debt = cursor.fetchone()
    cursor.execute("UPDATE repair_debts SET status = 'paid' WHERE id = ?", (debt_id,))
    conn.commit()
    conn.close()
    if debt:
        name, telegram_id, total_amount, paid_amount = debt
        if telegram_id:
            try:
                await bot.send_message(
                    chat_id=telegram_id,
                    text=f"✅ *Долг за ремонт закрыт*\n💰 Оплачено: *{total_amount} zł*"
                )
            except Exception:
                pass
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=f"✅ *Долг за ремонт №{debt_id} закрыт*\n👤 Клиент: *{name}*\n"
                         f"💰 Всего оплачено: *{total_amount} zł*"
                )
            except Exception:
                pass
    await callback.answer("Долг закрыт.")
    await send_repair_debts_list(callback)

@router.callback_query(F.data == "repair_exit")
async def exit_repair_debts(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.edit_text("↩️ Вы вышли из панели долгов за ремонт.")
    await callback.message.answer("Главное меню:", reply_markup=get_admin_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("close_"))
async def cb_close_rent(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.clear()
    rent_id = int(callback.data.split("_")[-1])
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT client_id, amount, due_amount, current_week, total_weeks, return_date, status, paid_days, period_days, total_days FROM rents WHERE id = ?", (rent_id,))
    rent_data = cursor.fetchone()
    
    if rent_data:
        client_id, weekly_amount, due_amount, current_week, total_weeks, old_date_str, status, paid_days, period_days, total_days = rent_data
        due_amount = due_amount or weekly_amount
        if status != "active":
            conn.close()
            await callback.answer("Контракт уже полностью оплачен.", show_alert=True)
            return
        cursor.execute("SELECT lang FROM clients WHERE tg_id = ?", (client_id,))
        lang_res = cursor.fetchone()
        lang = lang_res[0] if lang_res else "ru"
        operation_key = f"rent:{rent_id}:{paid_days}:{period_days}:manual"
        due_amount = due_amount or weekly_amount
        if not record_operation(cursor, operation_key, "rent_payment_manual", client_id, due_amount, f"Ручная оплата аренды №{rent_id}"):
            conn.close()
            await callback.answer("Этот платёж уже обработан.", show_alert=True)
            return
        
        try:
            await bot.send_message(chat_id=client_id, text=TEXTS[lang]["thank_you"].format(amount=due_amount))
        except: pass
        
        new_paid_days = paid_days + period_days
        if new_paid_days < total_days:
            next_week = current_week + 1
            old_date = parse_date(old_date_str)
            next_period_days = min(7, total_days - new_paid_days)
            next_due_amount = math.ceil(weekly_amount * next_period_days / 7)
            new_date = (old_date + datetime.timedelta(days=next_period_days)).strftime("%d.%m.%Y")
            cursor.execute(
                "UPDATE rents SET current_week = ?, paid_days = ?, period_days = ?, due_amount = ?, "
                "return_date = ?, is_notified = 0, prepayment_notified = 0, "
                "last_overdue_notified_at = NULL WHERE id = ?",
                (next_week, new_paid_days, next_period_days, next_due_amount, new_date, rent_id),
            )
            await callback.message.edit_text(f"💳 Оплачено {period_days} дн. на сумму {due_amount} zł! Следующий период: {next_period_days} дн. (до {new_date}).")
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=f"💳 *Ручная оплата аренды*\n\n"
                             f"👤 Клиент: *{client_id}*\n"
                             f"💵 Списано/отмечено: *{due_amount} zł*\n"
                             f"🚲 Оплачено дней: *{new_paid_days} из {total_days}*"
                    )
                except Exception:
                    pass
        else:
            cursor.execute("UPDATE rents SET status = 'completed', paid_days = total_days, is_notified = 1 WHERE id = ?", (rent_id,))
            await callback.message.edit_text(f"🎉 Все {total_days} дней оплачены! Контракт закрыт.")
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=f"🎉 *Контракт полностью оплачен вручную*\n\n"
                             f"👤 Клиент: *{client_id}*\n💵 Оплачено: *{due_amount} zł*\n"
                             f"🚲 Все {total_days} дней закрыты."
                    )
                except Exception:
                    pass
    conn.commit()
    conn.close()
    await callback.answer()

@router.callback_query(F.data.startswith("extend_"))
async def cb_extend_rent(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    rent_id = int(callback.data.split("_")[-1])
    await state.update_data(extend_rent_id=rent_id)
    await callback.message.answer("На сколько **ДОПОЛНИТЕЛЬНЫХ дней** перенести срок?\n*(Введите число кратное 7, например: 7, 14, 21, 28 или 0 для принудительного сброса автосписания):*")
    await state.set_state(RentStates.waiting_for_extra_days)
    await callback.answer()

@router.callback_query(F.data.startswith("delete_rent_"))
async def cb_delete_rent(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    rent_id = int(callback.data.rsplit("_", 1)[1])
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT client_name FROM rents WHERE id = ?", (rent_id,))
    rent = cursor.fetchone()
    if not rent:
        conn.close()
        await callback.answer("Контракт уже удалён.", show_alert=True)
        return

    cursor.execute("DELETE FROM rents WHERE id = ?", (rent_id,))
    conn.commit()
    conn.close()
    await callback.message.edit_text(
        f"🗑 Контракт №{rent_id} клиента *{rent[0]}* удалён.\n"
        "Кошелёк клиента сохранён."
    )
    await callback.answer()

@router.message(RentStates.waiting_for_extra_days)
async def process_extra_days(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS or not message.text.isdigit(): return
    extra_days = int(message.text)
    if extra_days <= 0:
        await message.answer("❌ Укажите положительное количество дополнительных дней.")
        return
    extra_weeks = math.ceil(extra_days / 7)
    
    user_data = await state.get_data()
    rent_id = user_data['extend_rent_id']
    
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT return_date, total_weeks, amount, paid_days, total_days FROM rents WHERE id = ?", (rent_id,))
    rent_info = cursor.fetchone()
    
    if rent_info:
        old_date_str, current_total_weeks, weekly_amount, paid_days, old_total_days = rent_info
        old_date = datetime.datetime.strptime(old_date_str, "%d.%m.%Y").date()
        
        new_date = (old_date + datetime.timedelta(days=extra_days)).strftime("%d.%m.%Y")
        new_total_weeks = current_total_weeks + extra_weeks
        new_total_amount = weekly_amount * new_total_weeks
        
        old_total_days = old_total_days or current_total_weeks * 7
        new_total_days = old_total_days + extra_days
        remaining_days = new_total_days - paid_days
        next_period_days = min(7, remaining_days)
        next_due_amount = math.ceil(weekly_amount * next_period_days / 7)
        if paid_days >= old_total_days:
            new_date = datetime.date.today().strftime("%d.%m.%Y")
        cursor.execute(
            "UPDATE rents SET return_date = ?, total_weeks = ?, total_days = ?, period_days = ?, due_amount = ?, total_month_amount = ?, is_notified = 0, status = 'active' WHERE id = ?",
            (new_date, new_total_weeks, new_total_days, next_period_days, next_due_amount, new_total_amount, rent_id)
        )
        conn.commit()
        
        await message.answer(f"📅 *Срок контракта №{rent_id} успешно продлен!*\n➕ Добавлено: {extra_days} дн. (+{extra_weeks} нед.)\n📅 Новая дата платежа: {new_date}\n📊 Всего недель стало: {new_total_weeks}\n💰 Общая сумма: {new_total_amount} zł")
    conn.close()
    await state.clear()

@router.message(F.text == "💰 Пополнить баланс кошелька")
async def start_deposit_buttons(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT client_id, client_name FROM rents")
    active_clients = cursor.fetchall()
    conn.close()
    
    if not active_clients:
        await message.answer("❌ Сейчас нет активных контрактов, кошелёк некому пополнять!")
        return
        
    builder = InlineKeyboardBuilder()
    for client_id, client_name in active_clients:
        builder.button(text=f"👤 {client_name}", callback_data=f"dep_cli_{client_id}")
    builder.adjust(1)
    await message.answer("👇 Выберите клиента для пополнения баланса из списка:", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("dep_cli_"))
async def cb_select_client_for_deposit(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    client_id = int(callback.data.split("_")[-1])
    
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT client_name FROM rents WHERE client_id = ? ORDER BY id DESC LIMIT 1", (client_id,))
    res = cursor.fetchone()
    conn.close()
    
    client_name = res[0] if res else f"ID: {client_id}"
    await state.update_data(deposit_client_id=client_id, deposit_client_name=client_name)
    await callback.message.answer(f"💰 Вы выбрали клиента: *{client_name}*\n\nВведите **сумму пополнения** в zł (только число):")
    await state.set_state(RentStates.waiting_for_deposit_amount)
    await callback.answer()

@router.message(RentStates.waiting_for_deposit_amount)
async def process_deposit_amount(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    if not message.text.isdigit() or int(message.text) <= 0:
        await message.answer("❌ Пожалуйста, введите корректное число (сумму в zł)!")
        return
        
    amount_to_add = int(message.text)
    user_data = await state.get_data()
    client_id = user_data['deposit_client_id']
    client_name = user_data['deposit_client_name']
    
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT lang, balance FROM clients WHERE tg_id = ?", (client_id,))
    client_res = cursor.fetchone()
    
    if not client_res:
        cursor.execute("INSERT OR REPLACE INTO clients (tg_id, name, balance) VALUES (?, ?, ?)", (client_id, client_name, amount_to_add))
        lang = "ru"
        new_balance = amount_to_add
    else:
        lang = client_res[0]
        current_balance = client_res[1]
        new_balance = current_balance + amount_to_add
        cursor.execute("UPDATE clients SET balance = ? WHERE tg_id = ?", (new_balance, client_id))
    record_operation(
        cursor,
        f"wallet:{message.from_user.id}:{client_id}:{amount_to_add}:{datetime.datetime.now().isoformat()}",
        "wallet_topup",
        client_id,
        amount_to_add,
        "Пополнение кошелька владельцем"
    )
    conn.commit()
    
    today_str = datetime.date.today().strftime("%d.%m.%Y")
    cursor.execute(
        "SELECT id, amount, due_amount, return_date, current_week, total_weeks, paid_days, period_days, total_days "
        "FROM rents WHERE client_id = ? AND status = 'active' ORDER BY id ASC",
        (client_id,)
    )
    rent_res = None
    today = datetime.date.today()
    for candidate in cursor.fetchall():
        candidate_date = datetime.datetime.strptime(candidate[3], "%d.%m.%Y").date()
        if candidate_date <= today:
            rent_res = candidate
            break
    
    if rent_res:
        rent_id, weekly_amount, rent_amount, return_date, current_week, total_weeks, paid_days, period_days, total_days = rent_res
        rent_amount = rent_amount or weekly_amount
        if new_balance >= rent_amount:
            new_balance = new_balance - rent_amount
            cursor.execute("UPDATE clients SET balance = ? WHERE tg_id = ?", (new_balance, client_id))
            
            new_paid_days = paid_days + period_days
            if new_paid_days < total_days:
                next_week = current_week + 1
                old_date = datetime.datetime.strptime(return_date, "%d.%m.%Y").date()
                next_period_days = min(7, total_days - new_paid_days)
                next_due_amount = math.ceil(weekly_amount * next_period_days / 7)
                new_date = (old_date + datetime.timedelta(days=next_period_days)).strftime("%d.%m.%Y")
                cursor.execute("UPDATE rents SET current_week = ?, paid_days = ?, period_days = ?, due_amount = ?, return_date = ?, is_notified = 0 WHERE id = ?", (next_week, new_paid_days, next_period_days, next_due_amount, new_date, rent_id))
                
                try:
                    msg_client = (
                        f"💳 *Автоматическая оплата аренды!*\n\nС кошелька списано: *{rent_amount} zł*\n🚲 Неделя: *{next_week} из {total_weeks}*\n📅 Следующий платеж: *{new_date}*\n💰 Остаток на балансе: *{new_balance} zł*"
                        if lang == "ru" else
                        f"💳 *Автоматична оплата оренди!*\n\nЗ гаманця списано: *{rent_amount} zł*\n🚲 Тиждень: *{next_week} з {total_weeks}*\n📅 Наступний платіж: *{new_date}*\n💰 Залишок на балансі: *{new_balance} zł*"
                    )
                    await bot.send_message(chat_id=client_id, text=msg_client)
                except: pass
            else:
                cursor.execute("UPDATE rents SET status = 'completed', paid_days = total_days, is_notified = 1 WHERE id = ?", (rent_id,))
                try:
                    await bot.send_message(chat_id=client_id, text=TEXTS[lang]["thank_you"].format(amount=rent_amount) + "\n🎉 Контракт успешно завершен!")
                except: pass
            
            await message.answer(f"✅ Баланс пополнен и **СРАЗУ СПИСАН** за аренду!\n\n👤 Клиент: *{client_name}*\n💵 Списано за период: *{rent_amount} zł*\n🚲 Оплачено дней: *{paid_days + period_days} из {total_days}*\n💰 Чистый остаток: *{new_balance} zł*", reply_markup=get_admin_keyboard())
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=f"💰 *Кошелёк пополнен и платёж списан*\n\n"
                             f"👤 Клиент: *{client_name}*\n"
                             f"➕ Внесено владельцу: *{amount_to_add} zł*\n"
                             f"💳 Списано за аренду: *{rent_amount} zł*\n"
                             f"💰 Остаток клиента: *{new_balance} zł*"
                    )
                except Exception:
                    pass
            conn.commit()
            conn.close()
            await check_deadlines()
            await state.clear()
            return

    conn.close()
    await check_deadlines()
    await message.answer(f"✅ Баланс успешно пополнен!\n👤 Клиент: *{client_name}*\n➕ Зачислено: +{amount_to_add} zł\n💰 Новый баланс: *{new_balance} zł*", reply_markup=get_admin_keyboard())
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=f"💰 *Кошелёк клиента пополнен*\n\n"
                     f"👤 Клиент: *{client_name}*\n"
                     f"➕ Внесено владельцу: *{amount_to_add} zł*\n"
                     f"💰 Баланс клиента: *{new_balance} zł*"
            )
        except Exception:
            pass
    try:
        msg_text = (
            f"🎉 *Баланс вашего кошелька пополнен!*\n\n➕ Зачислено: *{amount_to_add} zł*\n💰 Текущий баланс: *{new_balance} zł*"
            if lang == "ru" else
            f"🎉 *Баланс вашого гаманця поповнено!*\n\n➕ Зараховано: *{amount_to_add} zł*\n💰 Поточний баланс: *{new_balance} zł*"
        )
        await bot.send_message(chat_id=client_id, text=msg_text)
    except: pass
    if rent_res:
        _, weekly_amount, rent_amount, _, _, _, _, _, _ = rent_res
        rent_amount = rent_amount or weekly_amount
        if new_balance < rent_amount:
            remaining = rent_amount - new_balance
            reminder = (
                f"⚠️ *Платёж за аренду пока не списан.*\n"
                f"Нужно ещё пополнить кошелёк на *{remaining} zł*.\n"
                f"К оплате за период: *{rent_amount} zł*."
            )
            try:
                await bot.send_message(chat_id=client_id, text=reminder)
            except Exception:
                pass
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=f"⚠️ *Недостаточно средств после пополнения*\n"
                             f"👤 Клиент: *{client_name}*\n"
                             f"💰 Баланс: *{new_balance} zł*, требуется *{rent_amount} zł*\n"
                             f"➕ Не хватает: *{remaining} zł*"
                    )
                except Exception:
                    pass
    await state.clear()

@router.message(F.text == "📊 Статистика доходов")
async def show_statistics(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM rents WHERE status = 'active'")
    total_contracts = cursor.fetchone()[0]
    cursor.execute("SELECT SUM(amount) FROM rents")
    res_flow = cursor.fetchone()[0]
    weekly_flow = res_flow if res_flow is not None else 0
    cursor.execute("SELECT SUM(total_month_amount) FROM rents")
    res_projected = cursor.fetchone()[0]
    total_projected = res_projected if res_projected is not None else 0
    conn.close()
    
    text = (
        f"📊 *ФИНАНСОВАЯ СТАТИСТИКА ПРОКАТА*\n\n"
        f"🚲 *Активных контрактов:* {total_contracts} шт.\n"
        f"💳 *Ожидаемый доход в неделю:* {weekly_flow} zł\n"
        f"💰 *Общая сумма всех контрактов:* {total_projected} zł\n\n"
        f"📈 Бот успешно контролирует все выплаты!"
    )
    await message.answer(text)

@router.message(F.text == "📜 История операций")
async def show_operations_history(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT operation_type, client_id, amount, description, created_at "
        "FROM operations ORDER BY id DESC LIMIT 30"
    )
    operations = cursor.fetchall()
    conn.close()
    if not operations:
        await message.answer("📜 История операций пока пуста.")
        return
    labels = {
        "wallet_topup": "Пополнение",
        "rent_payment_auto": "Автооплата аренды",
        "rent_payment_manual": "Ручная оплата аренды",
        "repair_payment_auto": "Автооплата ремонта",
        "repair_payment_manual": "Ручная оплата ремонта",
    }
    lines = ["📜 *Последние операции:*\n"]
    for operation_type, client_id, amount, description, created_at in operations:
        label = labels.get(operation_type, operation_type)
        lines.append(f"• `{created_at}` — *{label}*: {amount} zł\n  {description} (ID: `{client_id}`)")
    await message.answer("\n".join(lines))

@router.message(F.text == "🧠 ИИ-Помощник")
async def open_ai_panel(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    text = "🧠 *ИИ-помощник владельца проката*\n\nИИ видит текущие контракты, кошельки, просрочки и долги за ремонт. Он может проанализировать ситуацию, подсказать действия или ответить на ваш вопрос."
    await message.answer(text, reply_markup=get_ai_panel_keyboard())

async def request_ai_answer(prompt):
    context = get_ai_business_context()
    groq_client = AsyncGroq(api_key=GROQ_API_KEY)
    response = await groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты — ИИ-ассистент владельца проката велосипедов и самокатов. "
                    "Используй предоставленную сводку базы данных, чтобы точно отвечать "
                    "по клиентам, долгам, кошелькам, аренде и ремонтам. Не выдумывай "
                    "данные. Если информации не хватает, прямо скажи об этом. Отвечай "
                    "по-русски, структурированно и кратко."
                ),
            },
            {"role": "user", "content": f"СВОДКА БАЗЫ:\n{context}\n\nЗАДАЧА ВЛАДЕЛЬЦА:\n{prompt}"},
        ],
    )
    return response.choices[0].message.content

async def send_ai_result(message, prompt):
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        ai_text = await request_ai_answer(prompt)
        try:
            await message.answer(ai_text, parse_mode="Markdown", reply_markup=get_ai_panel_keyboard())
        except Exception:
            await message.answer(ai_text, reply_markup=get_ai_panel_keyboard())
    except Exception:
        logger.exception("Ошибка ИИ-панели")
        await message.answer(
            "❌ ИИ временно недоступен. Проверьте настройки API и повторите попытку.",
            reply_markup=get_ai_panel_keyboard(),
        )

@router.callback_query(F.data == "ai_debt_analysis")
async def cb_ai_debt_analysis(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    await callback.message.answer("⏳ Анализирую текущие долги, просрочки и балансы...")
    await send_ai_result(
        callback.message,
        "Составь список клиентов с долгами и просрочками. Для каждого укажи сумму, "
        "тип долга и конкретное действие владельца. Отдельно укажи клиентов без средств.",
    )
    await callback.answer()

@router.callback_query(F.data == "ai_recommendations")
async def cb_ai_recommendations(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    await callback.message.answer("⏳ Формирую рекомендации по работе проката...")
    await send_ai_result(
        callback.message,
        "Проанализируй финансовое состояние проката. Дай 5 приоритетных рекомендаций "
        "по взысканию долгов, пополнению кошельков, аренде и ремонтам.",
    )
    await callback.answer()


@router.callback_query(F.data == "ai_custom_question")
async def cb_ai_custom(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await callback.message.answer("❓ Введите любой ваш вопрос или задачу для ИИ обычным текстом (без всяких команд):")
    await state.set_state(RentStates.waiting_for_ai_prompt)
    await callback.answer()

# 3. Режим свободного вопроса к ИИ
@router.message(RentStates.waiting_for_ai_prompt)
async def process_ai_custom(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    prompt = message.text.strip()
    if not prompt:
        await message.answer("❌ Вопрос не может быть пустым.")
        return
    
    await send_ai_result(message, prompt)
    await state.clear()



@router.callback_query(F.data == "ai_exit")
async def cb_ai_exit(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.clear()
    await callback.message.edit_text("↩️ Вы вышли из ИИ-панели.", reply_markup=None)
    await callback.message.answer("Главное меню админа:", reply_markup=get_admin_keyboard())
    await callback.answer()

@router.message(F.text == "❌ Очистить всю базу")
async def clear_database_cmd(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer(
        "⚠️ *Удаление всей базы*\n\n"
        "Будут удалены все контракты, клиенты и балансы.\n"
        "Для подтверждения введите PIN-код:"
    )
    await state.set_state(RentStates.waiting_for_clear_pin)

@router.message(RentStates.waiting_for_clear_pin)
async def process_clear_database_pin(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    if message.text.strip() != "7777":
        await message.answer("❌ Неверный PIN. Очистка отменена.")
        await state.clear()
        return

    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM rents")
    cursor.execute("DELETE FROM clients")
    cursor.execute("DELETE FROM repair_debts")
    cursor.execute("DELETE FROM operations")
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer(
        "✅ Вся база очищена: удалены все контракты, клиенты и балансы.",
        reply_markup=get_admin_keyboard()
    )

async def main():
    init_db()
    backup_database()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_deadlines, "interval", minutes=1)
    scheduler.add_job(backup_database, "interval", hours=24)
    scheduler.start()
    print("Bot started successfully!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
