python
import html
import time
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.enums import ContentType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from .config import Settings
from .db import Database
from .domain import Gender
from .keyboards import (
    Button,
    adult_confirmation,
    chat_menu,
    gender_picker,
    main_menu,
    payment_button,
    profile_actions,
)
from .services import Matchmaker
from .states import Registration

RULES = """<b>Правила анонимного чата</b>

1. Только для пользователей старше 18 лет.
2. Нельзя угрожать, оскорблять, спамить и отправлять незаконный контент.
3. Не отправляй адрес, телефон, документы и другие личные данные незнакомцам.
4. Сообщения копируются собеседнику без ссылки на твой аккаунт и не сохраняются в базе.
5. Жалоба завершит диалог и навсегда заблокирует повторную встречу этой пары."""

UNSAFE_CONTENT = {
    ContentType.CONTACT,
    ContentType.LOCATION,
    ContentType.VENUE,
}

router = Router()
relay_last_sent: dict[int, float] = {}


def build_router(settings: Settings, db: Database, matchmaker: Matchmaker) -> Router:
    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        if message.from_user is None:
            return

        user_id = message.from_user.id
        user = await db.get_user(user_id)
        if user is not None:
            await message.answer(
                "Ты уже зарегистрирован!", reply_markup=main_menu()
            )
            return

        await message.answer(
            "Привет! Добро пожаловать в анонимный чат.",
            reply_markup=adult_confirmation(),
        )

    @router.callback_query(F.data == "adult_yes")
    async def adult_yes(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        if callback.message is None or not isinstance(callback.message, Message):
            return

        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Отлично! Начнем регистрацию. Укажи свой пол:",
            reply_markup=gender_picker(),
        )
        await state.set_state(Registration.gender)

    @router.callback_query(F.data == "adult_no")
    async def adult_no(callback: CallbackQuery) -> None:
        await callback.answer()
        if callback.message is None or not isinstance(callback.message, Message):
            return

        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "К сожалению, этот чат предназначен только для лиц старше 18 лет."
        )

    @router.callback_query(Registration.gender, F.data.startswith("gender_"))
    async def process_gender(callback: CallbackQuery, state: FSMContext) -> None:
        await callback
.answer()
        if callback.message is None or not isinstance(callback.message, Message):
            return

        gender_str = callback.data.split("_")[1]
        gender = Gender.MALE if gender_str == "male" else Gender.FEMALE
        await state.update_data(gender=gender)

        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Теперь укажи свой возраст (число от 18 до 100):"
        )
        await state.set_state(Registration.age)

    @router.message(Registration.age)
    async def process_age(message: Message, state: FSMContext) -> None:
        if message.text is None:
            return

        try:
            age = int(message.text)
            if not (18 <= age <= 100):
                raise ValueError()
        except ValueError:
            await message.answer("Пожалуйста, введи корректный возраст (число от 18 до 100):")
            return

        await state.update_data(age=age)
        await message.answer(
            "Кого ты ищешь?",
            reply_markup=gender_picker(is_search=True),
        )
        await state.set_state(Registration.search_gender)

    @router.callback_query(Registration.search_gender, F.data.startswith("search_gender_"))
    async def process_search_gender(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        if callback.message is None or not isinstance(callback.message, Message):
            return

        gender_str = callback.data.split("_")[2]
        if gender_str == "any":
            search_gender = None
        else:
            search_gender = Gender.MALE if gender_str == "male" else Gender.FEMALE

        user_data = await state.get_data()
        await state.clear()

        if callback.from_user is None:
            return

        user_id = callback.from_user.id
        await db.create_user(
            user_id=user_id,
            gender=user_data["gender"],
            age=user_data["age"],
            search_gender=search_gender,
        )

        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Регистрация успешно завершена!", reply_markup=main_menu()
        )

    @router.message(F.text == Button.SEARCH.value)
    async def search(message: Message) -> None:
        if message.from_user is None:
            return

        user_id = message.from_user.id
        user = await db.get_user(user_id)
        if user is None:
            await message.answer("Сначала пройди регистрацию /start")
            return

        conversation = await db.get_active_conversation(user_id)
        if conversation is not None:
            await message.answer("Ты уже общаешься в чате!")
            return

        await matchmaker.search(user_id, show_waiting=True)

    @router.message(F.text == Button.STOP.value)
    async def stop(message: Message) -> None:
        if message.from_user is None:
            return

        user_id = message.from_user.id
        conversation = await db.get_active_conversation(user_id)
        if conversation is None:
            if await db.is_waiting(user_id):
                await db.remove_from_queue(user_id)
                await message.answer("Поиск собеседника остановлен.", reply_markup=main_menu())
            else:
                await message.answer("Ты сейчас не находишься в поиске.", reply_markup=main_menu())
            return

        await matchmaker.stop(user_id)
        partner_id = conversation.partner_of(user_id)
        await message.answer("Диалог завершен.", reply_markup=main_menu())
        await message.bot.send_message(
            partner_id, "Собеседник завершил диалог.", reply_markup=main_menu()
        )

    @router.message(F.text == Button.PROFILE.value)
    async def profile(message: Message) -> None:
        if message.from_user is None:
            return

        user_id = message.from_user.id
        user = await db.get_user(user_id)
        if user is None:
            await message.answer("Сначала пройди регистрацию /start")
return

        g = "Мужской 👦" if user.gender == Gender.MALE else "Женский 👧"
        if user.search_gender is None:
            sg = "Все равно 🌐"
        else:
            sg = "Парня 👦" if user.search_gender == Gender.MALE else "Девушку 👧"

        text = (
            f"<b>Твой профиль:</b>\n\n"
            f"Пол: {g}\n"
            f"Возраст: {user.age}\n"
            f"Ищешь: {sg}"
        )
        await message.answer(text, reply_markup=profile_actions())

    @router.callback_query(F.data == "edit_profile")
    async def edit_profile(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        if callback.message is None or not isinstance(callback.message, Message):
            return

        if callback.from_user is None:
            return

        user_id = callback.from_user.id
        await db.delete_user(user_id)
        await callback.message.answer(
            "Давай обновим профиль. Укажи свой пол:",
            reply_markup=gender_picker(),
        )
        await state.set_state(Registration.gender)

    @router.message(F.text == Button.RULES.value)
    async def rules(message: Message) -> None:
        await message.answer(RULES)

    @router.message(F.text == Button.REPORT.value)
    async def report(message: Message) -> None:
        if message.from_user is None:
            return

        user_id = message.from_user.id
        conversation = await db.get_active_conversation(user_id)
        if conversation is None:
            await message.answer("Ты сейчас не общаешься в чате.")
            return

        partner_id = conversation.partner_of(user_id)
        await db.add_report(reporter_id=user_id, reported_id=partner_id)
        await matchmaker.stop(user_id)

        await message.answer(
            "Жалоба отправлена. Собеседник заблокирован и диалог завершен.",
            reply_markup=main_menu(),
        )
        await message.bot.send_message(
            partner_id,
            "Собеседник завершил диалог.",
            reply_markup=main_menu(),
        )

    @router.message(StateFilter(None))
    async def relay(message: Message, bot: Bot) -> None:
        if message.from_user is None:
            return

        user_id = message.from_user.id
        conversation = await db.get_active_conversation(user_id)
        if conversation is None:
            if await db.is_waiting(user_id):
                await message.answer("Поиск еще идёт. Я напишу, когда кто-то найдётся.")
            else:
                await message.answer("Сначала найди собеседника.", reply_markup=main_menu())
            return

        if message.content_type in UNSAFE_CONTENT:
            await message.answer(
                "Контакты и геолокацию я не пересылаю, чтобы сохранить анонимность."
            )
            return

        now = time.monotonic()
        if now - relay_last_sent.get(user_id, 0.0) < 0.3:
            return

        relay_last_sent[user_id] = now
        partner_id = conversation.partner_of(user_id)
        try:
            await bot.copy_message(
                chat_id=partner_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                protect_content=True,
            )
        except TelegramForbiddenError:
            await matchmaker.stop(partner_id)
            await message.answer(
                "Собеседник стал недоступен. Ищу нового...",
                reply_markup=main_menu(),
            )
            await matchmaker.search(user_id, show_waiting=False)
        except TelegramBadRequest:
            await message.answer("Такой тип сообщения Telegram не разрешает скопировать.")

    return router
