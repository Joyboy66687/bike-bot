from aiogram import F, types
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

def register(router, admin_router, deps):
    sqlite3 = deps.sqlite3; DATABASE_PATH = deps.DATABASE_PATH
    escape_md = deps.escape_md
    @admin_router.message(F.text == "📊 Статистика доходов")
    async def show_statistics(message: types.Message):
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

    @admin_router.message(F.text == "📜 История операций")
    async def show_operations_history(message: types.Message):
        await render_history_page(message, 0)

    def history_page_keyboard(page, has_next):
        builder = InlineKeyboardBuilder()
        if page:
            builder.button(text="◀️ Раньше", callback_data=f"history_page_{page - 1}")
        if has_next:
            builder.button(text="Позже ▶️", callback_data=f"history_page_{page + 1}")
        builder.adjust(2)
        return builder.as_markup() if page or has_next else None

    async def render_history_page(target, page):
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT operation_type, client_id, amount, description, created_at "
            "FROM operations ORDER BY id DESC LIMIT 15 OFFSET ?", (page * 15,)
        )
        operations = cursor.fetchall()
        conn.close()
        if not operations:
            text = "📜 История операций пока пуста." if page == 0 else "Больше операций нет."
            if isinstance(target, types.CallbackQuery):
                await target.answer(text, show_alert=True)
            else:
                await target.answer(text)
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
            lines.append(f"• `{created_at}` — *{label}*: {amount} zł\n  {escape_md(description)} (ID: `{client_id}`)")
        has_next = len(operations) == 15
        text = "\n".join(lines)
        markup = history_page_keyboard(page, has_next)
        if isinstance(target, types.CallbackQuery):
            await target.message.edit_text(text, reply_markup=markup)
            await target.answer()
        else:
            await target.answer(text, reply_markup=markup)

    @admin_router.callback_query(F.data.startswith("history_page_"))
    async def history_page_callback(callback: types.CallbackQuery):
        await render_history_page(callback, int(callback.data.rsplit("_", 1)[1]))


def register_cleanup(admin_router, deps):
    sqlite3 = deps.sqlite3; DATABASE_PATH = deps.DATABASE_PATH
    RentStates = deps.RentStates; CLEAR_DB_PIN = deps.CLEAR_DB_PIN
    get_admin_keyboard = deps.get_admin_keyboard
    @admin_router.message(F.text == "❌ Очистить всю базу")
    async def clear_database_cmd(message: types.Message, state: FSMContext):
        await message.answer(
            "⚠️ *Удаление всей базы*\n\n"
            "Будут удалены все контракты, клиенты и балансы.\n"
            "Для подтверждения введите PIN-код:"
        )
        await state.set_state(RentStates.waiting_for_clear_pin)

    @admin_router.message(RentStates.waiting_for_clear_pin)
    async def process_clear_database_pin(message: types.Message, state: FSMContext):
        if message.text.strip() != CLEAR_DB_PIN:
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


    @admin_router.message(~F.text.startswith("/start"))
    async def fallback_admin(message: types.Message, state: FSMContext):
        await state.clear()
        await message.answer(
            "Не понял команду. Похоже, диалог сбросился (например, после перезапуска бота). "
            "Выберите действие в меню:",
            reply_markup=get_admin_keyboard()
        )
