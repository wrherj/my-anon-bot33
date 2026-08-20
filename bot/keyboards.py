from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


class Button:
    SEARCH = "🔎 Найти собеседника"
    NEXT = "➡️ Следующий"
    STOP = "⏹ Остановить"
    REPORT = "🚫 Пожаловаться"
    PROFILE = "👤 Моя анкета"
    SUBSCRIPTION = "⭐ Подписка"
    RULES = "📋 Правила"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=Button.SEARCH)],
            [
                KeyboardButton(text=Button.PROFILE),
                KeyboardButton(text=Button.SUBSCRIPTION),
            ],
            [KeyboardButton(text=Button.RULES)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбери действие",
    )


def chat_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=Button.NEXT), KeyboardButton(text=Button.STOP)],
            [KeyboardButton(text=Button.REPORT), KeyboardButton(text=Button.SUBSCRIPTION)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Напиши анонимное сообщение",
    )


def adult_confirmation() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Мне уже есть 18", callback_data="adult:yes")]
        ]
    )


def gender_picker() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👨 Мужчина", callback_data="gender:male"),
                InlineKeyboardButton(text="👩 Девушка", callback_data="gender:female"),
            ]
        ]
    )


def profile_actions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить анкету", callback_data="profile:edit")]
        ]
    )


def payment_button(invoice_link: str, price: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Купить за {price} ⭐",
                    url=invoice_link,
                )
            ]
        ]
    )
