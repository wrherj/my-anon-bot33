import os
from pydantic_core import SecretStr

class Settings:
    def __init__(self):
        # Берем токен из переменной BOT_TOKEN на Рендере
        token_val = os.getenv("BOT_TOKEN", "ТВОЙ_ДЕФОЛТНЫЙ_ТОКЕН")
        self.BOT_TOKEN = SecretStr(token_val)
        
        # Если в проекте используются другие настройки, добавляем их сюда:
        self.admins = []  # Список ID админов, если надо
