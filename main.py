import asyncio
import sqlite3
import datetime
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

BOT_TOKEN = "8988963212:AAHy95addyo8bf3IIDIycr-HPHapVUuBe_c"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
dp = Dispatcher()
router = Router()
scheduler = AsyncIOScheduler()

# ВШИТЫ ДВА АДМИНА: ВЫ И ВАШ ДРУГ (Впишите свои реальные Telegram ID)
ADMIN_IDS = [
    8737972065,  # Ваш ID
    6465909197   # ID друга
]

# СЕКРЕТНЫЙ ПИН-КОД ДЛЯ ОЧИСТКИ БАЗЫ
SHIELD_PIN = "7777"

class RentStates(StatesGroup):
    waiting_for_client_id = State()
    waiting_for_custom_name = State()  
    waiting_for_total_days = State()
    waiting_for_amount = State()
    waiting_for_days = State()
    waiting_for_new_amount = State() 
    waiting_for_extra_days = State()  
    waiting_for_pin = State() # Тексты уведомлений на двух языках для клиентов
TEXTS = {
    "ru": { 
        "welcome": "🚲 Привет, {name}!\nДобро пожаловать в прокат.\n\nПередай владельцу свой **ID**: `{id}`.\nЯ сам автоматически напомню тебе, когда придет время внести еженедельную оплату!",
        "invoice": "🚲 *Оформлен долгосрочный контракт!*\n\n👤 Имя: *{c_name}*\n📅 Общий срок: *{total_days} дней*\n💳 Еженедельный платеж: *{amount} zł*\n⏳ Ближайшая оплата: *{date}*\n\nБот будет автоматически напоминать вам об оплате каждые 7 дней.",
        "remind": "🔔 *Напоминание о еженедельной оплате!*\n\nСегодня ({date}) необходимо внести оплату за велосипед по вашему контракту.\n🚲 Неделя: *{week_num} из {total_weeks}*\n💰 К оплате: *{amount} zł*.",
        "thank_you": "🎉 *Спасибо!* Ваш еженедельный платеж на сумму *{amount} zł* успешно получен. Контракт продлен! 🚲",
    },
    "uk": {
        "welcome": "🚲 Привіт, {name}!\nЛаскаво просимо до прокату.\n\nПередай власнику свій **ID**: `{id}`.\nЯ сам автоматично нагадаю тобі, коли прийде час внести щотижневу оплату!",
        "invoice": "🚲 *Оформлено довгостроковий контракт!*\n\n👤 Ім'я: *{c_name}*\n📅 Загальний термін: *{total_days} днів*\n💳 Щотижневий платіж: *{amount} zł*\n⏳ Найближча оплата: *{date}*\n\nБот буде автоматично нагадувати вам про оплату кожні 7 днів.",
        "remind": "🔔 *Нагадування про щотижневу оплату!*\n\nСьогодні ({date}) необхідно внести оплату за велосипед за вашим контрактом.\n🚲 Тиждень: *{week_num} з {total_weeks}*\n💰 До сплати: *{amount} zł*.",
        "thank_you": "🎉 *Дякуємо!* Ваш щотижневий платіж на суму *{amount} zł* успішно отримано. Контракт продовжено! 🚲",
    }
}

async def set_bot_commands(bot: Bot):
    commands = [types.BotCommand(command="start", description="🔄 Перезапустить / Restart")]
    await bot.set_my_commands(commands)

def init_db():
    conn = sqlite3.connect("debts.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER,
            client_name TEXT,
            amount INTEGER,
            return_date TEXT,
            total_month_amount INTEGER,
            current_week INTEGER DEFAULT 1,
            total_weeks INTEGER DEFAULT 4,
            is_notified INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            tg_id INTEGER PRIMARY KEY, 
            name TEXT, 
            lang TEXT DEFAULT 'ru'
        )
    """)
    conn.commit()
    conn.close()
def get_admin_keyboard():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="➕ Оформить гибкий контракт")],
            [types.KeyboardButton(text="📋 Список всех долгов")],
            [types.KeyboardButton(text="❌ Очистить всю базу")]
        ],
        resize_keyboard=True
    )

def get_lang_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🇷🇺 Русский", callback_data="setlang_ru")
    builder.button(text="🇺🇦 Українська", callback_data="setlang_uk")
    return builder.as_markup()

def get_rent_inline_keyboard(rent_id):
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Изменить сумму", callback_data=f"edit_amt_{rent_id}")
    builder.button(text="📅 Продлить срок", callback_data=f"extend_{rent_id}")
    builder.button(text="✅ Оплачена неделя", callback_data=f"close_{rent_id}")
    builder.button(text="🗑 Вычеркнуть полностью", callback_data=f"drop_{rent_id}")
    builder.adjust(2, 2)
    return builder.as_markup()

async def check_deadlines():
    today_str = datetime.date.today().strftime("%d.%m.%Y")
    conn = sqlite3.connect("debts.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, client_id, client_name, amount, return_date, current_week, total_weeks FROM rents WHERE return_date = ? AND is_notified = 0", (today_str,))
    active_rents = cursor.fetchall()
    for rent in active_rents:
        rent_id, client_id, client_name, amount, return_date, current_week, total_weeks = rent
        cursor.execute("SELECT lang FROM clients WHERE tg_id = ?", (client_id,))
        lang_res = cursor.fetchone()
        lang = lang_res[0] if lang_res else "ru"
        try:
            await bot.send_message(
                chat_id=client_id,
                text=TEXTS[lang]["remind"].format(date=return_date, week_num=current_week, total_weeks=total_weeks, amount=amount)
            )
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        chat_id=admin_id, 
                        text=f"⚠️ *Срок еженедельной оплаты!*\n\n🆔 *Номер долга:* {rent_id}\n👤 *Клиент:* {client_name}\n🚲 *Неделя:* {current_week} из {total_weeks}\n💰 *Сумма:* {amount} zł"
                    )
                except: pass
            cursor.execute("UPDATE rents SET is_notified = 1 WHERE id = ?", (rent_id,)) 
        except: pass
    conn.commit()
    conn.close()

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    if user_id in ADMIN_IDS:
        await message.answer("👑 Привет, Владелец! Используй меню:", reply_markup=get_admin_keyboard())
    else:
        await message.answer("Выбери язык бота / Оберіть мову бота:", reply_markup=get_lang_keyboard())

@router.callback_query(F.data.startswith("setlang_"))
async def process_set_lang(callback: types.CallbackQuery):
    lang = callback.data.split("_")[-1]
    user_id = callback.from_user.id
    user_name = callback.from_user.full_name
    conn = sqlite3.connect("debts.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO clients (tg_id, name, lang) VALUES (?, ?, ?)", (user_id, user_name, lang))
    conn.commit()
    conn.close()
    welcome_msg = TEXTS[lang]["welcome"].format(name=user_name, id=user_id)
    await callback.message.edit_text(welcome_msg)
    await callback.answer()
@router.message(F.text == "➕ Оформить гибкий контракт")
async def start_rent(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await message.answer("Вставьте **Telegram ID клиента**:")
    await state.set_state(RentStates.waiting_for_client_id)

@router.message(RentStates.waiting_for_client_id)
async def process_client_id(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Только цифры!")
        return
    client_id = int(message.text)
    conn = sqlite3.connect("debts.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM clients WHERE tg_id = ?", (client_id,))
    client = cursor.fetchone()
    conn.close()
    if not client:
        await message.answer("❌ Пусть клиент сначала нажмет СТАРТ и выберет язык в этом боте.")
        await state.clear()
        return
    await state.update_data(client_id=client_id)
    await message.answer("Как зовут клиента? Введите имя (или заметку):")
    await state.set_state(RentStates.waiting_for_custom_name)

@router.message(RentStates.waiting_for_custom_name)
async def process_custom_name(message: types.Message, state: FSMContext):
    await state.update_data(client_name=message.text)
    await message.answer("На сколько дней оформляется контракт?\n*(Введите, например, 30, 60, 90 или 365):*")
    await state.set_state(RentStates.waiting_for_total_days)

@router.message(RentStates.waiting_for_total_days)
async def process_total_days(message: types.Message, state: FSMContext):
    if not message.text.isdigit() or int(message.text) == 0:
        await message.answer("Пожалуйста, введите корректное число дней!")
        return
    total_days = int(message.text)
    total_weeks = (total_days + 6) // 7
    await state.update_data(total_days=total_days, total_weeks=total_weeks)
    await message.answer(f"⏳ Срок: {total_days} дней ({total_weeks} нед.)\n\nВведите **стоимость за ОДНУ НЕДЕЛЮ** в **zł**:")
    await state.set_state(RentStates.waiting_for_amount)

@router.message(RentStates.waiting_for_amount)
async def process_amount(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Введите число!")
        return
    weekly_payment = int(message.text)
    user_data = await state.get_data()
    total_weeks = user_data['total_weeks']
    total_amount = weekly_payment * total_weeks
    
    await state.update_data(total_amount=total_amount, weekly_payment=weekly_payment)
    await message.answer(
        f"📊 *Автоматический расчет контракта:*\n"
        f"💳 Стоимость в неделю: {weekly_payment} zł\n"
        f"📅 Количество недель: {total_weeks}\n"
        f"💰 *ОБЩАЯ СУММА ЗА ВЕСЬ СРОК:* *{total_amount} zł*\n\n"
        f"Дней до первой оплаты? *(7 — стандарт, 0 — для теста прямо сейчас)*:"
    )
    await state.set_state(RentStates.waiting_for_days)
@router.message(RentStates.waiting_for_days)
async def process_days(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return
    days = int(message.text)
    user_data = await state.get_data()
    return_date = (datetime.date.today() + datetime.timedelta(days=days)).strftime("%d.%m.%Y")
    
    try:
        conn = sqlite3.connect("debts.db")
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO rents (client_id, client_name, amount, return_date, total_month_amount, current_week, total_weeks, is_notified) 
            VALUES (?, ?, ?, ?, ?, 1, ?, 0)
        """, (user_data['client_id'], user_data['client_name'], user_data['weekly_payment'], return_date, user_data['total_amount'], user_data['total_weeks']))
        conn.commit()
        
        cursor.execute("SELECT lang FROM clients WHERE tg_id = ?", (user_data['client_id'],))
        lang_res = cursor.fetchone()
        lang = lang_res[0] if lang_res else "ru"
        conn.close()

        await message.answer(f"✅ Контракт оформлен!\n👤 Клиент: {user_data['client_name']}\n📅 Срок: {user_data['total_days']} дн. ({user_data['total_weeks']} нед.)\n💳 В неделю: {user_data['weekly_payment']} zł\n💰 Общий итог: {user_data['total_amount']} zł\n📅 Оплата до: {return_date}", reply_markup=get_admin_keyboard())
        try:
            await bot.send_message(chat_id=user_data['client_id'], text=TEXTS[lang]["invoice"].format(c_name=user_data['client_name'], total_days=user_data['total_days'], amount=user_data['weekly_payment'], date=return_date))
        except: pass
    except Exception as e:
        await message.answer(f"❌ Ошибка базы данных: {e}")
    await state.clear()

@router.message(F.text == "📋 Список всех долгов")
async def show_debts(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    conn = sqlite3.connect("debts.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, client_name, amount, return_date, total_month_amount, current_week, total_weeks FROM rents")
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        await message.answer("🎉 Активных контрактов нет!")
        return
    await message.answer("📋 **База активных контрактов:**")
    for row in rows:
        rent_id, name, amount, date, total, week, total_weeks = row
        text = f"🆔 *Номер долга:* {rent_id}\n👤 *Клиент:* {name}\n🚲 *Период:* Неделя {week} из {total_weeks}\n💰 *Долг за неделю:* {amount} zł\n📊 *Всего за контракт:* {total} zł\n📅 *Срок оплаты:* {date}"
        await message.answer(text, reply_markup=get_rent_inline_keyboard(rent_id))

@router.callback_query(F.data.startswith("close_"))
async def cb_close_rent(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    rent_id = int(callback.data.split("_")[-1])
    conn = sqlite3.connect("debts.db")
    cursor = conn.cursor()
    cursor.execute("SELECT client_id, amount, current_week, total_weeks FROM rents WHERE id = ?", (rent_id,))
    rent_data = cursor.fetchone()
    if rent_data:
        client_id, amount, current_week, total_weeks = rent_data
        cursor.execute("SELECT lang FROM clients WHERE tg_id = ?", (client_id,))
        lang_res = cursor.fetchone()
        lang = lang_res[0] if lang_res else "ru"
        try:
            await bot.send_message(chat_id=client_id, text=TEXTS[lang]["thank_you"].format(amount=amount))
        except: pass
        if current_week < total_weeks:
            next_week = current_week + 1
            new_date = (datetime.date.today() + datetime.timedelta(days=7)).strftime("%d.%m.%Y")
            cursor.execute("UPDATE rents SET current_week = ?, return_date = ?, is_notified = 0 WHERE id = ?", (next_week, new_date, rent_id))
            await callback.message.edit_text(f"💳 Оплачена неделя {current_week}! Переключено на неделю {next_week} из {total_weeks} (до {new_date}).")
        else:
            cursor.execute("DELETE FROM rents WHERE id = ?", (rent_id,))
            await callback.message.edit_text(f"🎉 Все {total_weeks} недель оплачены! Контракт закрыт.")
    conn.commit()
    conn.close()
    await callback.answer()

@router.callback_query(F.data.startswith("drop_"))
async def cb_drop_rent(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    rent_id = int(callback.data.split("_")[-1])
    conn = sqlite3.connect("debts.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM rents WHERE id = ?", (rent_id,))
    conn.commit()
    conn.close()
    await callback.message.edit_text("🗑 Контракт полностью вычеркнут и удален из базы.")
    await callback.answer("Вычеркнуто")

@router.callback_query(F.data.startswith("edit_amt_"))
async def cb_edit_amount(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    rent_id = int(callback.data.split("_")[-1])
    await state.update_data(edit_rent_id=rent_id)
    await callback.message.answer("Введите **НОВУЮ сумму за неделю** в zł:")
    await state.set_state(RentStates.waiting_for_new_amount)
    await callback.answer()

@router.message(RentStates.waiting_for_new_amount)
async def process_new_amount(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS or not message.text.isdigit(): return
    new_weekly_amount = int(message.text)
    user_data = await state.get_data()
    rent_id = user_data['edit_rent_id']
    conn = sqlite3.connect("debts.db")
    cursor = conn.cursor()
    cursor.execute("SELECT total_weeks FROM rents WHERE id = ?", (rent_id,))
    res = cursor.fetchone()
    if res:
        total_weeks = res[0]
        new_total_amount = new_weekly_amount * total_weeks
        cursor.execute("UPDATE rents SET amount = ?, total_month_amount = ? WHERE id = ?", (new_weekly_amount, new_total_amount, rent_id))
        conn.commit()
        await message.answer(f"💰 Сумма недели изменена на {new_weekly_amount} zł.\n📊 Общий итог пересчитан: *{new_total_amount} zł*!")
    conn.close()
    await state.clear()

@router.callback_query(F.data.startswith("extend_"))
async def cb_extend_rent(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    rent_id = int(callback.data.split("_")[-1])
    await state.update_data(extend_rent_id=rent_id)
    await callback.message.answer("На сколько **ДОПОЛНИТЕЛЬНЫХ дней** перенести срок?:")
    await state.set_state(RentStates.waiting_for_extra_days)
    await callback.answer()

@router.message(RentStates.waiting_for_extra_days)
async def process_extra_days(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS or not message.text.isdigit(): return
    extra_days = int(message.text)
    user_data = await state.get_data()
    conn = sqlite3.connect("debts.db")
    cursor = conn.cursor()
    cursor.execute("SELECT return_date FROM rents WHERE id = ?", (user_data['extend_rent_id'],))
    old_date_tuple = cursor.fetchone()
    if old_date_tuple:
        old_date = datetime.datetime.strptime(old_date_tuple[0], "%d.%m.%Y").date()
        new_date = (old_date + datetime.timedelta(days=extra_days)).strftime("%d.%m.%Y")
        cursor.execute("UPDATE rents SET return_date = ?, is_notified = 0 WHERE id = ?", (new_date, user_data['extend_rent_id']))
        conn.commit()
        await message.answer(f"📅 Срок перенесен! Новая дата: {new_date}")
    conn.close()
    await state.clear()

@router.message(F.text == "❌ Очистить всю базу")
async def clear_debts_request(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await message.answer("⚠️ *ВНИМАНИЕ!* Вы собираетесь стереть ВСЕХ клиентов и контракты.\n\nВведите четырехзначный **секретный ПИН-код** для подтверждения:")
    await state.set_state(RentStates.waiting_for_pin)

@router.message(RentStates.waiting_for_pin)
async def process_pin(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    if message.text == SHIELD_PIN:
        conn = sqlite3.connect("debts.db")
        cursor = conn.cursor()
        cursor.execute("DELETE FROM rents")
        conn.commit()
        conn.close()
        await message.answer("🗑 База данных успешно очищена. Все активные контракты удалены.", reply_markup=get_admin_keyboard())
    else:
        await message.answer("❌ *Неверный ПИН-код!* Действие отменено. База данных в безопасности.", reply_markup=get_admin_keyboard())
    await state.clear()

async def main():
    init_db()
    dp.include_router(router)
    await set_bot_commands(bot)
    scheduler.add_job(check_deadlines, "interval", minutes=1)
    scheduler.start()
    print("Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

