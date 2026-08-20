import unittest
from pathlib import Path

from aiogram import Bot

from bot.config import Settings
from bot.db import Database
from bot.handlers import build_router
from bot.services import Matchmaker


class RouterConstructionTests(unittest.TestCase):
    def test_router_builds_with_current_aiogram_api(self) -> None:
        settings = Settings(
            bot_token="123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
            database_path=Path("unused.db"),
        )
        bot = Bot(settings.bot_token)
        database = Database(settings.database_path)
        matchmaker = Matchmaker(database, bot, settings)

        router = build_router(settings, database, matchmaker)

        self.assertIn("message", router.resolve_used_update_types())
        self.assertIn("pre_checkout_query", router.resolve_used_update_types())


if __name__ == "__main__":
    unittest.main()
