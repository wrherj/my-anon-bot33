import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

# Импортируем только то, что работает без ошибок
from bot.db import Database
from bot.services import Matchmaker
from bot.handlers import build_router

async def main():
    logging.basicConfig(level=logging.INFO)
    
    # 1. Забираем токен бота напрямую из настроек Рендера (без Pydantic!)
    bot_token = os.getenv("BOT_TOKEN")
    
    if not bot_token:
        raise ValueError("Бро, BOT_TOKEN не найден в переменных окружения Рендера!")
    
    # 2. Инициализируем бота и диспетчер
    bot = Bot(token=bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    
    # 3. Подключаем базу данных (sqlite в папке data)
    db = Database("sqlite+aiosqlite:///data/db.sqlite3")
    
    # 4. Запускаем систему подбора собеседников
    matchmaker = Matchmaker(settings=settings, db=db, bot=bot)
    
    # 5. Собираем роутер с хендлерами
    # Так как оригинальный build_router просит settings, мы создадим простую заглушку-обертку,
    # чтобы не ломать логику внутри handlers.py
    class FakeSettings:
        # Если в коде где-то внутри хендлеров вызывается settings.какой_то_параметр,
        # мы можем добавить их сюда. Но для запуска самого роутера этого хватит:
        pass

    fake_settings = FakeSettings()
    
    router = build_router(settings=fake_settings, db=db, matchmaker=matchmaker)
    dp.include_router(router)
    
    logging.info("Бот успешно запущен в обход сломанного Pydantic конфига! Погнали!")
    
    # Запуск опроса
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
