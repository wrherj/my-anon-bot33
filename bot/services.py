import asyncio
import logging
import math
import time

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from .config import Settings
from .db import Database
from .domain import Conversation, MatchResult
from .keyboards import chat_menu, main_menu

logger = logging.getLogger(__name__)


class Matchmaker:
    def __init__(self, db: Database, bot: Bot, settings: Settings) -> None:
        self.db = db
        self.bot = bot
        self.settings = settings

    async def send(self, user_id: int, text: str, **kwargs: object) -> bool:
        try:
            await self.bot.send_message(user_id, text, **kwargs)
            return True
        except TelegramForbiddenError:
            await self.db.leave_queue(user_id)
            logger.info("User %s blocked the bot", user_id)
            return False
        except TelegramBadRequest as exc:
            logger.warning("Cannot send message to %s: %s", user_id, exc)
            return False

    async def search(self, user_id: int, *, show_waiting: bool = True) -> MatchResult:
        result = await self.db.join_or_match(
            user_id,
            self.settings.free_chat_seconds,
        )
        if result.status == "matched" and result.conversation:
            await self._announce_match(result, user_id)
        elif result.status == "waiting" and show_waiting:
            await self.send(
                user_id,
                "🔎 Ищу подходящего собеседника...\n\n"
                "Можно закрыть Telegram. Я напишу, когда кто-то найдётся.",
                reply_markup=main_menu(),
            )
        elif result.status == "profile_required":
            await self.send(user_id, "Сначала заполни анкету через /start.")
        elif result.status == "banned":
            await self.send(user_id, "Доступ к боту ограничен.")
        return result

    async def _announce_match(self, result: MatchResult, initiator_id: int) -> None:
        conversation = result.conversation
        if conversation is None:
            return
        partner_id = conversation.partner_of(initiator_id)
        duration = self._duration_text(conversation)
        initiator_text = (
            "✅ Собеседник найден!\n"
            f"Возраст: <b>{result.partner_age}</b>\n"
            f"{duration}\n\n"
            "Пиши сообщение, я перешлю его анонимно."
        )
        partner_text = (
            "✅ Собеседник найден!\n"
            f"Возраст: <b>{result.user_age}</b>\n"
            f"{duration}\n\n"
            "Пиши сообщение, я перешлю его анонимно."
        )
        initiator_ok, partner_ok = await asyncio.gather(
            self.send(initiator_id, initiator_text, reply_markup=chat_menu()),
            self.send(partner_id, partner_text, reply_markup=chat_menu()),
        )
        if not initiator_ok or not partner_ok:
            missing_id = initiator_id if not initiator_ok else partner_id
            reachable_id = partner_id if missing_id == initiator_id else initiator_id
            await self.db.end_active_conversation(
                missing_id,
                "unreachable",
                requeue_actor=False,
                requeue_partner=True,
            )
            await self.send(
                reachable_id,
                "Собеседник оказался недоступен. Ищу другого...",
                reply_markup=main_menu(),
            )
            await self.search(reachable_id, show_waiting=False)

    def _duration_text(self, conversation: Conversation) -> str:
        if conversation.ends_at is None:
            return "⭐ У этой пары нет ограничения по времени."
        seconds_left = max(0, conversation.ends_at - int(time.time()))
        minutes_left = max(1, math.ceil(seconds_left / 60))
        return f"⏳ Бесплатный диалог: {minutes_left} мин. Потом собеседник сменится."

    async def next_partner(self, user_id: int) -> None:
        ended = await self.db.end_active_conversation(
            user_id,
            "next",
            requeue_actor=True,
            requeue_partner=True,
        )
        if ended is None:
            if await self.db.is_waiting(user_id):
                await self.send(user_id, "Ты уже в поиске.", reply_markup=main_menu())
            else:
                await self.search(user_id)
            return
        await asyncio.gather(
            self.send(user_id, "➡️ Ищу нового собеседника...", reply_markup=main_menu()),
            self.send(
                ended.partner_id,
                "Собеседник переключился. Уже ищу тебе нового...",
                reply_markup=main_menu(),
            ),
        )
        await self.search(user_id, show_waiting=False)
        await self.search(ended.partner_id, show_waiting=False)

    async def stop(self, user_id: int) -> None:
        await self.db.leave_queue(user_id)
        ended = await self.db.end_active_conversation(
            user_id,
            "stop",
            requeue_actor=False,
            requeue_partner=True,
        )
        await self.send(user_id, "Диалог остановлен.", reply_markup=main_menu())
        if ended:
            await self.send(
                ended.partner_id,
                "Собеседник вышел. Ищу тебе нового...",
                reply_markup=main_menu(),
            )
            await self.search(ended.partner_id, show_waiting=False)

    async def report(self, user_id: int) -> None:
        conversation = await self.db.get_active_conversation(user_id)
        if conversation is None:
            await self.send(user_id, "Сейчас у тебя нет активного диалога.")
            return
        partner_id = conversation.partner_of(user_id)
        await self.db.add_report_and_block(user_id, partner_id, conversation.id)
        ended = await self.db.end_active_conversation(
            user_id,
            "report",
            requeue_actor=True,
            requeue_partner=True,
        )
        await self.send(
            user_id,
            "Жалоба принята. Этот человек больше тебе не попадётся. Ищу другого...",
            reply_markup=main_menu(),
        )
        if ended:
            await self.send(
                partner_id,
                "Диалог завершён. Ищу нового собеседника...",
                reply_markup=main_menu(),
            )
        await self.search(user_id, show_waiting=False)
        if ended:
            await self.search(partner_id, show_waiting=False)

    async def process_expired(self) -> None:
        newly_limited = await self.db.restore_free_timers(self.settings.free_chat_seconds)
        for conversation in newly_limited:
            text = (
                "⭐ Оплаченный период закончился. У этой пары снова действует "
                f"бесплатный таймер: {self.settings.free_chat_minutes} мин."
            )
            await asyncio.gather(
                self.send(conversation.user1_id, text, reply_markup=chat_menu()),
                self.send(conversation.user2_id, text, reply_markup=chat_menu()),
            )
        expired = await self.db.expire_due()
        for conversation in expired:
            text = (
                "⏰ Время бесплатного диалога закончилось.\n"
                "Общение не останавливается: уже ищу тебе нового собеседника."
            )
            await asyncio.gather(
                self.send(conversation.user1_id, text, reply_markup=main_menu()),
                self.send(conversation.user2_id, text, reply_markup=main_menu()),
            )
            await self.search(conversation.user1_id, show_waiting=False)
            await self.search(conversation.user2_id, show_waiting=False)

    async def expiry_loop(self) -> None:
        while True:
            try:
                await self.process_expired()
                for user_id in await self.db.waiting_user_ids():
                    await self.search(user_id, show_waiting=False)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Matchmaking background loop failed")
            await asyncio.sleep(self.settings.expiry_check_seconds)
