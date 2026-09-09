"""Common user and menu handlers.

The module owns the handler implementations and receives application
dependencies explicitly so it never imports ``main`` (and cannot create a
circular import).
"""
from aiogram import F, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder


def register(router, admin_router, deps):
    TEXTS = deps.TEXTS
    ADMIN_IDS = deps.ADMIN_IDS
    DATABASE_PATH = deps.DATABASE_PATH
    bot = deps.bot
    escape_md = deps.escape_md
    get_admin_keyboard = deps.get_admin_keyboard
    RentStates = deps.RentStates
    sqlite3 = deps.sqlite3

    @router.message(CommandStart())
    async def cmd_start(message: types.Message, state: FSMContext):
        user_id = message.from_user.id
        if user_id in ADMIN_IDS:
            await message.answer("👑 Привет, Владелец! Используй меню:",
                                 reply_markup=get_admin_keyboard())
            return
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT lang FROM clients WHERE tg_id = ?", (user_id,))
        res = cursor.fetchone()
        conn.close()
        if res:
            lang = res[0]
            client_menu = types.ReplyKeyboardMarkup(
                keyboard=[[types.KeyboardButton(
                    text="🎒 Мой кошелёк / Мій гаманець")]], resize_keyboard=True)
            await message.answer(
                TEXTS[lang]["welcome"].format(
                    name=escape_md(message.from_user.full_name), id=user_id),
                reply_markup=client_menu)
        else:
            builder = InlineKeyboardBuilder()
            builder.button(text="🇷🇺 Русский", callback_data="setlang_ru")
            builder.button(text="🇺🇦 Українська", callback_data="setlang_uk")
            await message.answer(
                "🚲 Выберите язык интерфейса / Оберіть мову інтерфейсу:",
                reply_markup=builder.as_markup())

    @admin_router.message(F.text == "⬅️ Назад в меню")
    async def back_to_admin_menu(message: types.Message, state: FSMContext):
        await state.clear()
        await message.answer("↩️ Текущий сценарий отменён. Главное меню:",
                             reply_markup=get_admin_keyboard())

    @router.callback_query(F.data.startswith("setlang_"))
    async def process_set_lang(callback: types.CallbackQuery):
        lang = callback.data.split("_")[1]
        if lang not in TEXTS:
            await callback.answer("Неизвестный язык", show_alert=True)
            return
        user_id = callback.from_user.id
        user_name = callback.from_user.full_name
        if user_id in ADMIN_IDS:
            await callback.message.answer(
                "👑 Вы зарегистрированы как владелец.",
                reply_markup=get_admin_keyboard())
            await callback.answer()
            return
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO clients (tg_id, name, lang, balance) "
            "VALUES (?, ?, ?, COALESCE((SELECT balance FROM clients "
            "WHERE tg_id = ?), 0))",
            (user_id, user_name, lang, user_id))
        conn.commit()
        conn.close()
        await callback.message.answer(
            TEXTS[lang]["welcome"].format(name=escape_md(user_name), id=user_id),
            reply_markup=types.ReplyKeyboardMarkup(
                keyboard=[[types.KeyboardButton(
                    text="🎒 Мой кошелёк / Мій гаманець")]], resize_keyboard=True))
        await callback.answer()

    @router.message(F.text == "🎒 Мой кошелёк / Мій гаманець")
    async def text_open_wallet(message: types.Message):
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT lang, balance FROM clients WHERE tg_id = ?",
                       (message.from_user.id,))
        res = cursor.fetchone()
        conn.close()
        lang = res[0] if res else "ru"
        balance = res[1] if res else 0
        await message.answer(TEXTS[lang]["wallet"].format(balance=balance))

    return (cmd_start, back_to_admin_menu, process_set_lang, text_open_wallet)
