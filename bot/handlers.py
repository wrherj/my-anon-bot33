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
5. Жалоба завершает диалог и навсегда блокирует повторную встречу этой пары.

Используя бот, ты соглашаешься с этими правилами."""


UNSAFE_CONTENT = {
    ContentType.CONTACT,
    ContentType.LOCATION,
    ContentType.VENUE,
}


def _unix_timestamp(value: datetime | int | None) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp())
    if isinstance(value, int):
        return value
    return int(time.time()) + 30 * 24 * 60 * 60


def build_router(settings: Settings, db: Database, matchmaker: Matchmaker) -> Router:
    router = Router(name="anonymous_chat")
    relay_last_sent: dict[int, float] = {}

    async def show_welcome(message: Message, state: FSMContext) -> None:
        await state.clear()
        if message.from_user is None:
            return
        await db.touch_user(message.from_user.id, message.from_user.username)
        user = await db.get_user(message.from_user.id)
        if user and user.is_banned:
            await message.answer("Доступ к боту ограничен.")
            return
        if not user or not user.is_complete:
            await message.answer(
                "Привет! Здесь можно анонимно общаться один на один.\n\n"
                "Бот подбирает пары мужчина-девушка. В бесплатной версии "
                f"собеседник меняется каждые {settings.free_chat_minutes} мин., "
                "но сам чат не прекращается. С подпиской ограничения по времени нет.\n\n"
                "Нажимая кнопку ниже, ты подтверждаешь, что тебе уже исполнилось 18 лет.",
                reply_markup=adult_confirmation(),
            )
            return
        await message.answer(
            "С возвращением. Нажми кнопку, и я найду собеседника.",
            reply_markup=chat_menu()
            if await db.get_active_conversation(message.from_user.id)
            else main_menu(),
        )

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await show_welcome(message, state)

    @router.callback_query(F.data == "adult:yes")
    async def confirm_adult(callback: CallbackQuery) -> None:
        await callback.answer()
        if callback.message:
            await callback.message.edit_text(
                "Укажи свой пол. Подбор всегда идёт с человеком другого пола.",
                reply_markup=gender_picker(),
            )

    @router.callback_query(F.data.startswith("gender:"))
    async def choose_gender(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.from_user is None:
            return
        value = (callback.data or "").split(":", 1)[1]
        if value not in {Gender.MALE.value, Gender.FEMALE.value}:
            await callback.answer("Неизвестный вариант", show_alert=True)
            return
        await db.touch_user(callback.from_user.id, callback.from_user.username)
        await state.update_data(gender=value)
        await state.set_state(Registration.age)
        await callback.answer()
        if callback.message:
            await callback.message.edit_text(
                f"Теперь напиши свой возраст числом от {settings.min_age} до {settings.max_age}."
            )

    @router.message(Registration.age)
    async def enter_age(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        value = (message.text or "").strip()
        if not value.isdigit():
            await message.answer("Напиши только число. Например: <b>24</b>.")
            return
        age = int(value)
        if not settings.min_age <= age <= settings.max_age:
            await message.answer(
                f"Возраст должен быть от {settings.min_age} до {settings.max_age}."
            )
            return
        data = await state.get_data()
        gender = Gender(data["gender"])
        await db.save_profile(message.from_user.id, gender, age)
        await state.clear()
        await message.answer(
            "Готово. Анкета сохранена, можно начинать.",
            reply_markup=main_menu(),
        )

    @router.callback_query(F.data == "profile:edit")
    async def edit_profile(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await callback.answer()
        if callback.message:
            await callback.message.edit_text("Выбери свой пол:", reply_markup=gender_picker())

    @router.message(Command("profile"))
    @router.message(F.text == Button.PROFILE)
    async def profile(message: Message) -> None:
        if message.from_user is None:
            return
        user = await db.get_user(message.from_user.id)
        if not user or not user.is_complete:
            await message.answer("Анкета ещё не заполнена. Нажми /start.")
            return
        subscription = (
            "активна" if (user.subscription_expires_at or 0) > int(time.time()) else "нет"
        )
        await message.answer(
            "<b>Твоя анкета</b>\n\n"
            f"Пол: {user.gender.label}\n"
            f"Возраст: {user.age}\n"
            f"Подписка: {subscription}",
            reply_markup=profile_actions(),
        )

    @router.message(Command("search"))
    @router.message(F.text == Button.SEARCH)
    async def search(message: Message) -> None:
        if message.from_user:
            await matchmaker.search(message.from_user.id)

    @router.message(Command("next"))
    @router.message(F.text == Button.NEXT)
    async def next_partner(message: Message) -> None:
        if message.from_user:
            await matchmaker.next_partner(message.from_user.id)

    @router.message(Command("stop"))
    @router.message(F.text == Button.STOP)
    async def stop(message: Message) -> None:
        if message.from_user:
            await matchmaker.stop(message.from_user.id)

    @router.message(Command("report"))
    @router.message(F.text == Button.REPORT)
    async def report(message: Message) -> None:
        if message.from_user:
            await matchmaker.report(message.from_user.id)

    @router.message(Command("rules"))
    @router.message(F.text == Button.RULES)
    async def rules(message: Message) -> None:
        await message.answer(RULES, reply_markup=main_menu())

    @router.message(Command("subscription"))
    @router.message(F.text == Button.SUBSCRIPTION)
    async def subscription(message: Message, bot: Bot) -> None:
        if message.from_user is None:
            return
        await db.touch_user(message.from_user.id, message.from_user.username)
        user = await db.get_user(message.from_user.id)
        now = int(time.time())
        if user and (user.subscription_expires_at or 0) > now:
            expires = datetime.fromtimestamp(user.subscription_expires_at or now).strftime(
                "%d.%m.%Y %H:%M"
            )
            await message.answer(
                "⭐ Подписка активна.\n"
                f"Оплаченный период: до <b>{expires}</b>.\n\n"
                "В активном диалоге ограничения по времени нет."
            )
            return
        payload = f"premium:30:{message.from_user.id}"
        invoice_link = await bot.create_invoice_link(
            title="Безлимитный анонимный чат",
            description="Сохраняй одного собеседника без автоматической смены в течение 30 дней.",
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[
                LabeledPrice(
                    label="Подписка на 30 дней",
                    amount=settings.subscription_price_stars,
                )
            ],
            subscription_period=30 * 24 * 60 * 60,
        )
        await message.answer(
            "<b>Подписка на 30 дней</b>\n\n"
            "• текущий собеседник не меняется по таймеру\n"
            "• кнопка Следующий остаётся доступной\n"
            "• подписка действует на всю пару, даже если у второго человека её нет\n\n"
            f"Цена: <b>{settings.subscription_price_stars} ⭐</b> в месяц. "
            "Подписка продлевается автоматически, пока ты её не отключишь в Telegram.",
            reply_markup=payment_button(invoice_link, settings.subscription_price_stars),
        )

    @router.pre_checkout_query()
    async def pre_checkout(query: PreCheckoutQuery) -> None:
        expected_payload = f"premium:30:{query.from_user.id}"
        valid = (
            query.invoice_payload == expected_payload
            and query.currency == "XTR"
            and query.total_amount == settings.subscription_price_stars
        )
        if valid:
            await query.answer(ok=True)
        else:
            await query.answer(
                ok=False,
                error_message="Счёт устарел. Вернись в бот и создай новый.",
            )

    @router.message(F.successful_payment)
    async def successful_payment(message: Message) -> None:
        if message.from_user is None or message.successful_payment is None:
            return
        await db.touch_user(message.from_user.id, message.from_user.username)
        payment = message.successful_payment
        expected_payload = f"premium:30:{message.from_user.id}"
        if (
            payment.invoice_payload != expected_payload
            or payment.currency != "XTR"
            or payment.total_amount != settings.subscription_price_stars
        ):
            await message.answer(
                "Платёж получен, но его нужно проверить вручную. Напиши "
                f"{html.escape(settings.support_contact)}."
            )
            return
        expiration = _unix_timestamp(payment.subscription_expiration_date)
        created = await db.record_payment(
            user_id=message.from_user.id,
            telegram_charge_id=payment.telegram_payment_charge_id,
            amount=payment.total_amount,
            currency=payment.currency,
            subscription_expires_at=expiration,
            is_recurring=bool(payment.is_recurring),
            is_first_recurring=bool(payment.is_first_recurring),
        )
        if not created:
            return
        await message.answer(
            "✅ Подписка активирована.\n\nЕсли ты уже общаешься, таймер этой пары снят.",
            reply_markup=chat_menu()
            if await db.get_active_conversation(message.from_user.id)
            else main_menu(),
        )

    @router.message(Command("paysupport"))
    async def payment_support(message: Message) -> None:
        await message.answer("По вопросам оплаты напиши: " + html.escape(settings.support_contact))

    @router.message(Command("stats"))
    async def stats(message: Message) -> None:
        if message.from_user is None or message.from_user.id not in settings.admin_id_set:
            return
        values = await db.stats()
        await message.answer(
            "<b>Статистика</b>\n\n"
            f"Пользователи: {values['users']}\n"
            f"В поиске: {values['waiting']}\n"
            f"Активные диалоги: {values['active_chats']}\n"
            f"Подписчики: {values['subscribers']}\n"
            f"Жалобы: {values['reports']}"
        )

    @router.message(StateFilter(None))
    async def relay(message: Message, bot: Bot) -> None:
        if message.from_user is None:
            return
        user_id = message.from_user.id
        conversation = await db.get_active_conversation(user_id)
        if conversation is None:
            if await db.is_waiting(user_id):
                await message.answer("Поиск ещё идёт. Я напишу, когда кто-то найдётся.")
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

