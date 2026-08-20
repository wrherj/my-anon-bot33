import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

# Импортируем родные модули из твоего проекта
from bot.config import Settings
from bot.db import Database
from bot.services import Matchmaker
from bot.handlers import build_router

async def main():
    logging.basicConfig(level=logging.INFO)
    
    # 1. Настройки
    settings = Settings()
    
    # 2. Инициализируем бота
    bot = Bot(token=settings.BOT_TOKEN.get_secret_value())
    dp = Dispatcher(storage=MemoryStorage())
    
    # 3. Подключаем базу данных и ОТКРЫВАЕМ коннект!
    db = Database("sqlite+aiosqlite:///data/db.sqlite3")
    await db.connect()  # ВОТ ЭТА СТРОЧКА СПАСЕТ НАШУ ЖОПУ!
    
    # 4. Запускаем матчмейкер
    matchmaker = Matchmaker(settings=settings, db=db, bot=bot)
    
    # 5. Собираем роутер
    router = build_router(settings=settings, db=db, matchmaker=matchmaker)
    dp.include_router(router)
    
    logging.info("Бот успешно запущен и БД подключена, бро!")
    
    # Запуск опроса
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

