from functools import cached_property
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str
    subscription_price_stars: int = Field(default=199, ge=1, le=10_000)
    free_chat_minutes: int = Field(default=15, ge=1, le=24 * 60)
    min_age: int = Field(default=18, ge=18, le=100)
    max_age: int = Field(default=80, ge=18, le=100)
    support_contact: str = "@your_support"
    admin_ids: str = ""
    database_path: Path = Path("data/anonymous_chat.db")
    expiry_check_seconds: int = Field(default=5, ge=1, le=60)

    @field_validator("bot_token")
    @classmethod
    def validate_token(cls, value: str) -> str:
        if not value or value.endswith("replace_me"):
            raise ValueError("Укажите настоящий BOT_TOKEN в файле .env")
        return value

    @cached_property
    def admin_id_set(self) -> set[int]:
        return {int(item.strip()) for item in self.admin_ids.split(",") if item.strip().isdigit()}

    @property
    def free_chat_seconds(self) -> int:
        return self.free_chat_minutes * 60
