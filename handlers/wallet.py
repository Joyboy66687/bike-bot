from aiogram import F, types
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

def register(router, admin_router, deps):
    math = deps.math; datetime = deps.datetime; sqlite3 = deps.sqlite3
    DATABASE_PATH = deps.DATABASE_PATH; bot = deps.bot; ADMIN_IDS = deps.ADMIN_IDS
    TEXTS = deps.TEXTS; TZ = deps.TZ; logger = deps.logger
    escape_md = deps.escape_md; RentStates = deps.RentStates
    get_admin_keyboard = deps.get_admin_keyboard; check_deadlines = deps.check_deadlines
    charge_rent_period = deps.charge_rent_period; record_operation = deps.record_operation
    @admin_router.message(F.text == "💰 Пополнить баланс кошелька")
    async def start_deposit_buttons(message: types.Message, state: FSMContext):
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        clients_by_id = {}
        cursor.execute("SELECT tg_id AS id, name FROM clients")
        for client_id, name in cursor.fetchall():
            clients_by_id[client_id] = name
        cursor.execute(
            "SELECT DISTINCT client_id AS id, client_name AS name "
            "FROM rents WHERE client_id IS NOT NULL"
        )
        for client_id, name in cursor.fetchall():
            if client_id not in clients_by_id or not clients_by_id[client_id]:
                clients_by_id[client_id] = name
        active_clients = list(clients_by_id.items())
        conn.close()
        
        if not active_clients:
            await message.answer("❌ В базе пока нет клиентов для пополнения.")
            return
            
        builder = InlineKeyboardBuilder()
        for client_id, client_name in active_clients:
            builder.button(text=f"👤 {escape_md(client_name)}", callback_data=f"dep_cli_{client_id}")
        builder.adjust(1)
        await message.answer("👇 Выберите клиента для пополнения баланса из списка:", reply_markup=builder.as_markup())

    @admin_router.callback_query(F.data.startswith("dep_cli_"))
    async def cb_select_client_for_deposit(callback: types.CallbackQuery, state: FSMContext):
        client_id = int(callback.data.split("_")[-1])
        
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COALESCE((SELECT name FROM clients WHERE tg_id = ?), "
            "(SELECT client_name FROM rents WHERE client_id = ? ORDER BY id DESC LIMIT 1))",
            (client_id, client_id),
        )
        res = cursor.fetchone()
        conn.close()
        
        client_name = res[0] if res else f"ID: {client_id}"
        await state.update_data(deposit_client_id=client_id, deposit_client_name=client_name)
        await callback.message.answer(f"💰 Вы выбрали клиента: *{escape_md(client_name)}*\n\nВведите **сумму пополнения** в zł (только число):")
        await state.set_state(RentStates.waiting_for_deposit_amount)
        await callback.answer()

    @admin_router.message(RentStates.waiting_for_deposit_amount)
    async def process_deposit_amount(message: types.Message, state: FSMContext):
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
        
        today_str = datetime.datetime.now(TZ).date().strftime("%d.%m.%Y")
        cursor.execute(
            "SELECT id, amount, due_amount, return_date, current_week, total_weeks, paid_days, period_days, total_days "
            "FROM rents WHERE client_id = ? AND status = 'active' ORDER BY id ASC",
            (client_id,)
        )
        rent_res = None
        today = datetime.datetime.now(TZ).date()
        for candidate in cursor.fetchall():
            candidate_date = datetime.datetime.strptime(candidate[3], "%d.%m.%Y").date()
            if candidate_date <= today:
                rent_res = candidate
                break
        
        if rent_res:
            rent_id, weekly_amount, rent_amount, return_date, current_week, total_weeks, paid_days, period_days, total_days = rent_res
            rent_amount = rent_amount or weekly_amount
            if new_balance >= rent_amount:
                result = charge_rent_period(cursor, {
                    "id": rent_id, "client_id": client_id, "client_name": client_name,
                    "amount": weekly_amount, "due_amount": rent_amount, "return_date": return_date,
                    "current_week": current_week, "total_weeks": total_weeks, "paid_days": paid_days,
                    "period_days": period_days, "total_days": total_days,
                }, "deposit")
                if result is None:
                    conn.close()
                    await state.clear()
                    return
                new_balance = result["new_balance"]
                new_paid_days = result["new_paid_days"]
                if not result["completed"]:
                    new_date = result["next_date"]
                    next_period_days = min(7, total_days - new_paid_days)
                    try:
                        msg_client = (
                            f"💳 *Автоматическая оплата аренды!*\n\nС кошелька списано: *{rent_amount} zł*\n🚲 Следующий период: *{next_period_days} дн.*\n📅 Следующий платеж: *{new_date}*\n💰 Остаток на балансе: *{new_balance} zł*"
                            if lang == "ru" else
                            f"💳 *Автоматична оплата оренди!*\n\nЗ гаманця списано: *{rent_amount} zł*\n🚲 Наступний період: *{next_period_days} дн.*\n📅 Наступний платіж: *{new_date}*\n💰 Залишок на балансі: *{new_balance} zł*"
                        )
                        await bot.send_message(chat_id=client_id, text=msg_client)
                    except Exception:
                        logger.exception("Не удалось отправить сообщение chat_id=%s", client_id)
                else:
                    try:
                        await bot.send_message(chat_id=client_id, text=TEXTS[lang]["thank_you"].format(amount=rent_amount) + "\n🎉 Контракт успешно завершен!")
                    except Exception:
                        logger.exception("Не удалось отправить сообщение chat_id=%s", client_id)
                
                await message.answer(f"✅ Баланс пополнен и **СРАЗУ СПИСАН** за аренду!\n\n👤 Клиент: *{escape_md(client_name)}*\n💵 Списано за период: *{rent_amount} zł*\n🚲 Оплачено дней: *{paid_days + period_days} из {total_days}*\n💰 Чистый остаток: *{new_balance} zł*", reply_markup=get_admin_keyboard())
                for admin_id in ADMIN_IDS:
                    try:
                        await bot.send_message(
                            chat_id=admin_id,
                            text=f"💰 *Кошелёк пополнен и платёж списан*\n\n"
                                 f"👤 Клиент: *{escape_md(client_name)}*\n"
                                 f"➕ Внесено владельцу: *{amount_to_add} zł*\n"
                                 f"💳 Списано за аренду: *{rent_amount} zł*\n"
                                 f"💰 Остаток клиента: *{new_balance} zł*"
                        )
                    except Exception:
                        logger.exception("Не удалось отправить сообщение")
                conn.commit()
                conn.close()
                await check_deadlines()
                await state.clear()
                return

        conn.close()
        await check_deadlines()
        await message.answer(f"✅ Баланс успешно пополнен!\n👤 Клиент: *{escape_md(client_name)}*\n➕ Зачислено: +{amount_to_add} zł\n💰 Новый баланс: *{new_balance} zł*", reply_markup=get_admin_keyboard())
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=f"💰 *Кошелёк клиента пополнен*\n\n"
                         f"👤 Клиент: *{escape_md(client_name)}*\n"
                         f"➕ Внесено владельцу: *{amount_to_add} zł*\n"
                         f"💰 Баланс клиента: *{new_balance} zł*"
                )
            except Exception:
                logger.exception("Не удалось отправить сообщение")
        try:
            msg_text = (
                f"🎉 *Баланс вашего кошелька пополнен!*\n\n➕ Зачислено: *{amount_to_add} zł*\n💰 Текущий баланс: *{new_balance} zł*"
                if lang == "ru" else
                f"🎉 *Баланс вашого гаманця поповнено!*\n\n➕ Зараховано: *{amount_to_add} zł*\n💰 Поточний баланс: *{new_balance} zł*"
            )
            await bot.send_message(chat_id=client_id, text=msg_text)
        except Exception:
            logger.exception("Не удалось отправить сообщение")
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
                    logger.exception("Не удалось отправить сообщение")
                for admin_id in ADMIN_IDS:
                    try:
                        await bot.send_message(
                            chat_id=admin_id,
                            text=f"⚠️ *Недостаточно средств после пополнения*\n"
                                 f"👤 Клиент: *{escape_md(client_name)}*\n"
                                 f"💰 Баланс: *{new_balance} zł*, требуется *{rent_amount} zł*\n"
                                 f"➕ Не хватает: *{remaining} zł*"
                        )
                    except Exception:
                        logger.exception("Не удалось отправить сообщение")
        await state.clear()
