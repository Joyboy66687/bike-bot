from aiogram import F, types
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

def register(router, admin_router, deps):
    math = deps.math; datetime = deps.datetime; sqlite3 = deps.sqlite3
    DATABASE_PATH = deps.DATABASE_PATH; bot = deps.bot; ADMIN_IDS = deps.ADMIN_IDS
    TEXTS = deps.TEXTS; TZ = deps.TZ; logger = deps.logger
    escape_md = deps.escape_md; RentStates = deps.RentStates
    check_deadlines = deps.check_deadlines; charge_rent_period = deps.charge_rent_period
    parse_date = deps.parse_date; should_send_overdue_reminder = deps.should_send_overdue_reminder
    get_repair_debts_keyboard = deps.get_repair_debts_keyboard; get_admin_keyboard = deps.get_admin_keyboard
    @admin_router.message(F.text == "➕ Оформить гибкий контракт")
    async def start_rent(message: types.Message, state: FSMContext):
        await message.answer("👤 Шаг 1: Введите **имя клиента**:")
        await state.set_state(RentStates.waiting_for_client_name)

    @admin_router.message(RentStates.waiting_for_client_name)
    async def process_client_name(message: types.Message, state: FSMContext):
        client_name = message.text.strip()
        if not client_name:
            await message.answer("❌ Имя не может быть пустым. Введите имя клиента:")
            return
        await state.update_data(client_name=client_name)
        await message.answer("🆔 Шаг 2: Введите **Telegram ID** клиента:")
        await state.set_state(RentStates.waiting_for_name)

    @admin_router.message(RentStates.waiting_for_name)
    async def process_client_id(message: types.Message, state: FSMContext):
        if not message.text or not message.text.isdigit() or int(message.text) <= 0:
            await message.answer("❌ Введите положительный числовой Telegram ID клиента.")
            return
        await state.update_data(c_id=int(message.text))
        await message.answer("💰 Шаг 3: Введите **цену за 1 неделю** аренды (в zł):")
        await state.set_state(RentStates.waiting_for_amount)

    @admin_router.message(RentStates.waiting_for_amount)
    async def process_amount(message: types.Message, state: FSMContext):
        if not message.text or not message.text.isdigit() or int(message.text) <= 0:
            await message.answer("❌ Введите положительный тариф за неделю целым числом.")
            return
        await state.update_data(amount=int(message.text))
        await message.answer("⏳ Шаг 4: На сколько **ДНЕЙ** оформляется аренда?\n*(Например: 90 дней):*")
        await state.set_state(RentStates.waiting_for_duration)

    @admin_router.message(RentStates.waiting_for_duration)
    async def process_duration(message: types.Message, state: FSMContext):
        if not message.text or not message.text.isdigit():
            await message.answer("❌ Введите срок аренды положительным целым числом дней.")
            return
        total_days = int(message.text)
        if total_days < 0:
            await message.answer("❌ Срок аренды не может быть отрицательным.")
            return
        weeks = max(1, math.ceil(total_days / 7))
        data = await state.get_data()
        weekly_amount = data['amount']
        period_days = min(7, total_days) if total_days else 1
        due_amount = math.ceil(weekly_amount * period_days / 7)
        client_id = data['c_id']
        total_month_amount = math.ceil(weekly_amount * total_days / 7) if total_days else due_amount
        
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT name, lang FROM clients WHERE tg_id = ?", (client_id,))
        c_res = cursor.fetchone()
        c_name = data.get("client_name") or (c_res[0] if c_res else f"ID: {client_id}")
        lang = c_res[1] if c_res else "ru"
        
        start_date = datetime.datetime.now(TZ).date()
        return_date = (
            start_date if total_days == 0 else start_date + datetime.timedelta(days=period_days)
        ).strftime("%d.%m.%Y")
        
        cursor.execute("""
            INSERT INTO rents (client_id, client_name, amount, return_date, total_month_amount, total_days, paid_days, period_days, due_amount, current_week, total_weeks, is_notified, status)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, 1, ?, 0, 'active')
        """, (client_id, c_name, weekly_amount, return_date, total_month_amount, total_days, period_days, due_amount, weeks))
        conn.commit()
        conn.close()
        
        await message.answer(f"✅ Контракт успешно создан для *{escape_md(c_name)}*!\nДедлайн первой недели: {return_date}", reply_markup=get_admin_keyboard())
        
        try:
            await bot.send_message(chat_id=client_id, text=TEXTS[lang]["invoice"].format(c_name=escape_md(c_name), total_days=total_days, amount=weekly_amount, date=return_date))
        except Exception:
            logger.exception("Не удалось отправить сообщение")
        if total_days == 0:
            await check_deadlines()
        await state.clear()

    @admin_router.message(F.text == "📋 Список всех долгов")
    async def list_debts(message: types.Message):
        await render_debts_page(message, 0)

    def debts_page_keyboard(page, has_next):
        builder = InlineKeyboardBuilder()
        if page:
            builder.button(text="◀️ Раньше", callback_data=f"debts_page_{page - 1}")
        if has_next:
            builder.button(text="Позже ▶️", callback_data=f"debts_page_{page + 1}")
        builder.adjust(2)
        return builder.as_markup() if page or has_next else None

    async def render_debts_page(target, page):
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, client_name, amount, due_amount, return_date, total_days, "
            "paid_days, current_week, total_weeks, client_id, status FROM rents "
            "ORDER BY id DESC LIMIT 5 OFFSET ?", (page * 5,))
        rents = cursor.fetchall()
        conn.close()
        if not rents:
            text = "📋 История контрактов пока пуста." if page == 0 else "Больше контрактов нет."
            if isinstance(target, types.CallbackQuery):
                await target.answer(text, show_alert=True)
            else:
                await target.answer(text)
            return
        has_next = len(rents) == 5
        messages = []
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
            text = f"🆔 *Контракт №{r_id}*\n👤 Клиент: *{escape_md(c_name)}* (ID: `{c_id}`)\n📌 Статус: *{status_text}*\n💳 Тариф: *{amount} zł/неделя*\n📅 Срок: *{total_days} дней* ({tot_w} периодов)\n💰 Общая сумма: *{total_contract_amount} zł*\n🚲 Оплачено дней: *{paid_days} из {total_days}*\n💵 Ближайшее списание: *{due_amount or amount} zł*\n⏳ Срок платежа: *{r_date}*"
            messages.append((text, builder.as_markup()))
        if isinstance(target, types.CallbackQuery):
            await target.message.edit_text(
                "\n\n".join(item[0] for item in messages),
                reply_markup=debts_page_keyboard(page, has_next),
            )
            await target.answer()
        else:
            for text, markup in messages:
                await target.answer(text, reply_markup=markup)
            if page or has_next:
                await target.answer(f"Страница {page + 1}", reply_markup=debts_page_keyboard(page, has_next))

    @admin_router.callback_query(F.data.startswith("debts_page_"))
    async def debts_page_callback(callback: types.CallbackQuery):
        await render_debts_page(callback, int(callback.data.rsplit("_", 1)[1]))


    @admin_router.callback_query(F.data.startswith("close_"))
    async def cb_close_rent(callback: types.CallbackQuery, state: FSMContext):
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
            result = charge_rent_period(cursor, {
                "id": rent_id, "client_id": client_id, "client_name": "",
                "amount": weekly_amount, "due_amount": due_amount, "return_date": old_date_str,
                "current_week": current_week, "total_weeks": total_weeks, "paid_days": paid_days,
                "period_days": period_days, "total_days": total_days,
            }, "manual")
            if result is None:
                conn.close()
                await callback.answer("Недостаточно средств или этот платёж уже обработан.", show_alert=True)
                return
            due_amount = result["charged"]
            new_paid_days = result["new_paid_days"]
            try:
                await bot.send_message(chat_id=client_id, text=TEXTS[lang]["thank_you"].format(amount=due_amount))
            except Exception:
                logger.exception("Не удалось отправить сообщение chat_id=%s", client_id)
            
            if not result["completed"]:
                new_date = result["next_date"]
                next_period_days = min(7, total_days - new_paid_days)
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
                        logger.exception("Не удалось отправить сообщение")
            else:
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
                        logger.exception("Не удалось отправить сообщение")
        conn.commit()
        conn.close()
        await callback.answer()

    @admin_router.callback_query(F.data.startswith("extend_"))
    async def cb_extend_rent(callback: types.CallbackQuery, state: FSMContext):
        rent_id = int(callback.data.split("_")[-1])
        await state.update_data(extend_rent_id=rent_id)
        await callback.message.answer("На сколько **ДОПОЛНИТЕЛЬНЫХ дней** перенести срок?\n*(Введите число кратное 7, например: 7, 14, 21, 28 или 0 для принудительного сброса автосписания):*")
        await state.set_state(RentStates.waiting_for_extra_days)
        await callback.answer()

    @admin_router.callback_query(F.data.startswith("delete_rent_"))
    async def cb_delete_rent(callback: types.CallbackQuery):

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
            f"🗑 Контракт №{rent_id} клиента *{escape_md(rent[0])}* удалён.\n"
            "Кошелёк клиента сохранён."
        )
        await callback.answer()

    @admin_router.message(RentStates.waiting_for_extra_days)
    async def process_extra_days(message: types.Message, state: FSMContext):
        if not message.text or not message.text.isdigit(): return
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
                new_date = datetime.datetime.now(TZ).date().strftime("%d.%m.%Y")
            cursor.execute(
                "UPDATE rents SET return_date = ?, total_weeks = ?, total_days = ?, period_days = ?, due_amount = ?, total_month_amount = ?, is_notified = 0, status = 'active' WHERE id = ?",
                (new_date, new_total_weeks, new_total_days, next_period_days, next_due_amount, new_total_amount, rent_id)
            )
            conn.commit()
            
            await message.answer(f"📅 *Срок контракта №{rent_id} успешно продлен!*\n➕ Добавлено: {extra_days} дн. (+{extra_weeks} нед.)\n📅 Новая дата платежа: {new_date}\n📊 Всего недель стало: {new_total_weeks}\n💰 Общая сумма: {new_total_amount} zł")
        conn.close()
        await state.clear()
