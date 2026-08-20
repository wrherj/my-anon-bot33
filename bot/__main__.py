import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

# Импортируем родные модули
from bot.config import Settings
from bot.db import Database
from bot.services import Matchmaker
from bot.handlers import build_router

# Хендлер для Рендера, чтобы он видел, что сайт "работает"
async def handle_index(request):
    return web.Response(text="Бот онлайн, бро! Система работает 24/7.")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle_index)
    runner = web.AppRunner(app)
    await runner.setup()
    # Рендер автоматически передает порт в переменную окружения PORT.
    # Если её нет (например, локально), берем дефолтный порт 10000.
    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Призрачный веб-сервер запущен на порту {port}")

async def main():
    logging.basicConfig(level=logging.INFO)
    
    # 1. Запуск фейкового веб-сервера для обхода блокировок Рендера
    await start_webserver()
    
    # 2. Инициализируем наш конфиг
    settings = Settings()
    
    # 3. Инициализируем бота
    bot = Bot(token=settings.BOT_TOKEN.get_secret_value())
    dp = Dispatcher(storage=MemoryStorage())
    
    # 4. Подключаем базу данных
    db = Database("sqlite+aiosqlite:///data/db.sqlite3")
    await db.connect()
    
    # 5. Запускаем матчмейкер
    matchmaker = Matchmaker(settings=settings, db=db, bot=bot)
    
    # 6. Собираем роутер
    router = build_router(settings=settings, db=db, matchmaker=matchmaker)
    dp.include_router(router)
    
    logging.info("Бот успешно запущен на оригинальной архитектуре, бро!")
    
    # Запуск опроса Телеграма
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
