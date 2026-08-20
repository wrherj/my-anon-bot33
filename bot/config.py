import os

class Settings:
    def __init__(self):
        # Забираем токен напрямую из переменной окружения
        token_val = os.getenv("BOT_TOKEN", "")
        
        # Оборачиваем его в фейковый класс с методом get_secret_value,
        # чтобы остальной оригинальный код бота не заметил подмены и не выдал ошибку!
        class SecretToken:
            def __init__(self, val):
                self.val = val
            def get_secret_value(self):
                return self.val
                
        self.BOT_TOKEN = SecretToken(token_val)
        self.admins = []
