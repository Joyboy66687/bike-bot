from aiogram import F, types
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

def register(router, admin_router, deps):
    sqlite3 = deps.sqlite3; DATABASE_PATH = deps.DATABASE_PATH
    bot = deps.bot; ADMIN_IDS = deps.ADMIN_IDS; logger = deps.logger
    escape_md = deps.escape_md; RentStates = deps.RentStates
    get_admin_keyboard = deps.get_admin_keyboard; check_deadlines = deps.check_deadlines
    record_operation = deps.record_operation
    def get_repair_debts_keyboard():
        builder = InlineKeyboardBuilder()
        builder.button(text="➕ Записать новый долг", callback_data="repair_add")
        builder.button(text="📋 Обновить список", callback_data="repair_list")
        builder.button(text="⬅️ В главное меню", callback_data="repair_exit")
        builder.adjust(1)
        return builder.as_markup()

    @admin_router.message(F.text == "🛠 Долги за ремонт")
    async def open_repair_debts_panel(message: types.Message):
        await message.answer(
            "🛠 *Панель долгов за ремонт*\n\n"
            "Здесь можно записать ремонт в долг, видеть остаток и отмечать частичные платежи.",
            reply_markup=get_repair_debts_keyboard()
        )

    @admin_router.callback_query(F.data == "repair_add")
    async def start_repair_debt(callback: types.CallbackQuery, state: FSMContext):
        await state.clear()
        await callback.message.answer("👤 Введите имя клиента или короткое описание, например: *Иван Петров*")
        await state.set_state(RentStates.waiting_for_repair_debtor_name)
        await callback.answer()

    @admin_router.message(RentStates.waiting_for_repair_debtor_name)
    async def process_repair_debtor_name(message: types.Message, state: FSMContext):
        name = message.text.strip()
        if not name:
            await message.answer("❌ Имя не может быть пустым.")
            return
        await state.update_data(repair_debtor_name=name)
        await message.answer("🆔 Введите Telegram ID клиента для уведомлений или `0`, если уведомлять только владельца:")
        await state.set_state(RentStates.waiting_for_repair_debtor_id)

    @admin_router.message(RentStates.waiting_for_repair_debtor_id)
    async def process_repair_debtor_id(message: types.Message, state: FSMContext):
        if not message.text.isdigit():
            await message.answer("❌ Введите числовой Telegram ID или `0`.")
            return
        telegram_id = int(message.text)
        await state.update_data(repair_telegram_id=telegram_id or None)
        await message.answer("💰 Введите полную стоимость ремонта в zł, например: *700*")
        await state.set_state(RentStates.waiting_for_repair_amount)

    @admin_router.message(RentStates.waiting_for_repair_amount)
    async def process_repair_amount(message: types.Message, state: FSMContext):
        if not message.text.isdigit() or int(message.text) <= 0:
            await message.answer("❌ Введите положительную сумму целым числом.")
            return
        await state.update_data(repair_total_amount=int(message.text))
        await message.answer("🔧 Что ремонтировали? Например: *Замена камеры и настройка тормозов*")
        await state.set_state(RentStates.waiting_for_repair_description)

    @admin_router.message(RentStates.waiting_for_repair_description)
    async def process_repair_description(message: types.Message, state: FSMContext):
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
            f"👤 Клиент: *{escape_md(data['repair_debtor_name'])}*\n"
            f"🔧 Работа: {escape_md(description)}\n"
            f"💰 Сумма: *{data['repair_total_amount']} zł*\n"
            f"📅 Создан: {created_at}",
            reply_markup=get_repair_debts_keyboard()
        )
        notification = (
            f"🛠 *У вас новый долг за ремонт №{debt_id}*\n\n"
            f"🔧 Работа: {escape_md(description)}\n💰 К оплате: *{data['repair_total_amount']} zł*\n"
            f"📅 Дата: {created_at}"
        )
        if data.get("repair_telegram_id"):
            try:
                await bot.send_message(chat_id=data["repair_telegram_id"], text=notification)
            except Exception:
                logger.exception("Не удалось отправить сообщение")
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=f"🛠 *Записан долг за ремонт №{debt_id}*\n👤 Клиент: *{escape_md(data['repair_debtor_name'])}*\n💰 Сумма: *{data['repair_total_amount']} zł*"
                )
            except Exception:
                logger.exception("Не удалось отправить сообщение")
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
                    f"🆔 *№{debt_id}* — *{escape_md(name)}*\n"
                    f"🔧 {escape_md(description)}\n"
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

    @admin_router.callback_query(F.data == "repair_list")
    async def repair_debts_list_callback(callback: types.CallbackQuery):
        await send_repair_debts_list(callback)

    @admin_router.callback_query(F.data.startswith("repair_pay_"))
    async def start_repair_payment(callback: types.CallbackQuery, state: FSMContext):
        debt_id = int(callback.data.rsplit("_", 1)[1])
        await state.update_data(repair_debt_id=debt_id)
        await callback.message.answer(f"💵 Введите сумму платежа по долгу №{debt_id} в zł:")
        await state.set_state(RentStates.waiting_for_repair_payment)
        await callback.answer()

    @admin_router.message(RentStates.waiting_for_repair_payment)
    async def process_repair_payment(message: types.Message, state: FSMContext):
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
            f"💳 Платёж *{payment} zł* записан для *{escape_md(name)}*.\n{status_text}",
            reply_markup=get_repair_debts_keyboard()
        )
        if telegram_id:
            try:
                await bot.send_message(
                    chat_id=telegram_id,
                    text=f"💳 *Платёж по долгу за ремонт получен: {payment} zł*\n"
                         f"👤 Клиент: {escape_md(name)}\n"
                         f"💰 Остаток: *{total_amount - new_paid} zł*"
                )
            except Exception:
                logger.exception("Не удалось отправить сообщение")
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=f"✅ *Оплата ремонта записана*\n👤 Клиент: *{escape_md(name)}*\n💵 Получено: *{payment} zł*\n"
                         f"💰 Остаток: *{total_amount - new_paid} zł*"
                )
            except Exception:
                logger.exception("Не удалось отправить сообщение")

    @admin_router.callback_query(F.data.startswith("repair_delete_"))
    async def delete_repair_debt(callback: types.CallbackQuery):
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
                    logger.exception("Не удалось отправить сообщение")
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=f"✅ *Долг за ремонт №{debt_id} закрыт*\n👤 Клиент: *{escape_md(name)}*\n"
                             f"💰 Всего оплачено: *{total_amount} zł*"
                    )
                except Exception:
                    logger.exception("Не удалось отправить сообщение")
        await callback.answer("Долг закрыт.")
        await send_repair_debts_list(callback)

    @admin_router.callback_query(F.data == "repair_exit")
    async def exit_repair_debts(callback: types.CallbackQuery, state: FSMContext):
        await callback.message.edit_text("↩️ Вы вышли из панели долгов за ремонт.")
        await callback.message.answer("Главное меню:", reply_markup=get_admin_keyboard())
        await callback.answer()
