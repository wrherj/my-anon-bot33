import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from .config import Settings
from .db import Database
from .handlers import build_router
from .services import Matchmaker


async def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    db = Database(settings.database_path)
    await db.connect()
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())
    matchmaker = Matchmaker(db, bot, settings)
    dispatcher.include_router(build_router(settings, db, matchmaker))

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="search", description="Найти собеседника"),
            BotCommand(command="next", description="Следующий собеседник"),
            BotCommand(command="stop", description="Остановить диалог"),
            BotCommand(command="profile", description="Моя анкета"),
            BotCommand(command="subscription", description="Подписка"),
            BotCommand(command="rules", description="Правила"),
            BotCommand(command="paysupport", description="Помощь с оплатой"),
        ]
    )
    background_task = asyncio.create_task(matchmaker.expiry_loop())
    try:
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        background_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await background_task
        await db.close()


def run() -> None:
    asyncio.run(main())
