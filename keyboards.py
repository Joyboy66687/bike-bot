"""Reusable keyboard builders kept independent from handler registration."""
from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_ai_panel_keyboard():
    builder = InlineKeyboardBuilder()
    for text, callback in (
        ("📊 Анализ долгов и оплат", "ai_debt_analysis"),
        ("💡 Рекомендации по прокату", "ai_recommendations"),
        ("❓ Задать свой вопрос ИИ", "ai_custom_question"),
        ("⬅️ Выйти из ИИ панели", "ai_exit"),
    ):
        builder.button(text=text, callback_data=callback)
    builder.adjust(1)
    return builder.as_markup()


def get_repair_debts_keyboard():
    builder = InlineKeyboardBuilder()
    for text, callback in (
        ("➕ Записать новый долг", "repair_add"),
        ("📋 Обновить список", "repair_list"),
        ("⬅️ В главное меню", "repair_exit"),
    ):
        builder.button(text=text, callback_data=callback)
    builder.adjust(1)
    return builder.as_markup()


def get_admin_keyboard(has_ai=True):
    rows = [
        [types.KeyboardButton(text="➕ Оформить гибкий контракт")],
        [types.KeyboardButton(text="📋 Список всех долгов")],
        [types.KeyboardButton(text="🛠 Долги за ремонт")],
        [types.KeyboardButton(text="💰 Пополнить баланс кошелька")],
    ]
    if has_ai:
        rows.append([types.KeyboardButton(text="🧠 ИИ-Помощник")])
    rows.extend([
        [types.KeyboardButton(text="📊 Статистика доходов")],
        [types.KeyboardButton(text="📜 История операций")],
        [types.KeyboardButton(text="❌ Очистить всю базу")],
        [types.KeyboardButton(text="⬅️ Назад в меню")],
    ])
    return types.ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)
