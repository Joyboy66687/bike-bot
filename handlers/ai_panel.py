from aiogram import F, types
from aiogram.fsm.context import FSMContext
from groq import AsyncGroq

def register(router, admin_router, deps):
    bot = deps.bot; logger = deps.logger; GROQ_API_KEY = deps.GROQ_API_KEY
    GROQ_MODEL = deps.GROQ_MODEL; RentStates = deps.RentStates
    get_ai_panel_keyboard = deps.get_ai_panel_keyboard
    get_admin_keyboard = deps.get_admin_keyboard
    get_ai_business_context = deps.get_ai_business_context
    @admin_router.message(F.text == "🧠 ИИ-Помощник")
    async def open_ai_panel(message: types.Message):
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

    @admin_router.callback_query(F.data == "ai_debt_analysis")
    async def cb_ai_debt_analysis(callback: types.CallbackQuery):
        await callback.message.answer("⏳ Анализирую текущие долги, просрочки и балансы...")
        await send_ai_result(
            callback.message,
            "Составь список клиентов с долгами и просрочками. Для каждого укажи сумму, "
            "тип долга и конкретное действие владельца. Отдельно укажи клиентов без средств.",
        )
        await callback.answer()

    @admin_router.callback_query(F.data == "ai_recommendations")
    async def cb_ai_recommendations(callback: types.CallbackQuery):
        await callback.message.answer("⏳ Формирую рекомендации по работе проката...")
        await send_ai_result(
            callback.message,
            "Проанализируй финансовое состояние проката. Дай 5 приоритетных рекомендаций "
            "по взысканию долгов, пополнению кошельков, аренде и ремонтам.",
        )
        await callback.answer()


    @admin_router.callback_query(F.data == "ai_custom_question")
    async def cb_ai_custom(callback: types.CallbackQuery, state: FSMContext):
        await callback.message.answer("❓ Введите любой ваш вопрос или задачу для ИИ обычным текстом (без всяких команд):")
        await state.set_state(RentStates.waiting_for_ai_prompt)
        await callback.answer()

    # 3. Режим свободного вопроса к ИИ
    @admin_router.message(RentStates.waiting_for_ai_prompt)
    async def process_ai_custom(message: types.Message, state: FSMContext):
        prompt = message.text.strip()
        if not prompt:
            await message.answer("❌ Вопрос не может быть пустым.")
            return
        
        await send_ai_result(message, prompt)
        await state.clear()



    @admin_router.callback_query(F.data == "ai_exit")
    async def cb_ai_exit(callback: types.CallbackQuery, state: FSMContext):
        await state.clear()
        await callback.message.edit_text("↩️ Вы вышли из ИИ-панели.", reply_markup=None)
        await callback.message.answer("Главное меню админа:", reply_markup=get_admin_keyboard())
        await callback.answer()
