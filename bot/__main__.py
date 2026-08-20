import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
# Импортируем наши обработчики (мы их сейчас обновим)
from bot.handlers import register_user_handlers
# Тут должен быть твой токен. На Рендере мы вынесем его в переменные окружения,
# но для быстрого старта можешь вписать его прямо сюда вместо "ТВОЙ_ТОКЕН":
BOT_TOKEN = "8639542935:AAGMhgjmeL7C6_XqFK6p60vcqgthabOOSNE"

logging.basicConfig(level=logging.INFO)

async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    
    # Регистрируем наши новые роутеры с логикой Stars, жалоб и лимитов
    register_user_handlers(dp)
    
    logging.info("Бот успешно запущен, бро! Погнали!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
