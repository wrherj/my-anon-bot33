from dataclasses import dataclass
from enum import StrEnum


class Gender(StrEnum):
    MALE = "male"
    FEMALE = "female"

    @property
    def label(self) -> str:
        return "мужчина" if self is Gender.MALE else "девушка"


@dataclass(slots=True, frozen=True)
class UserProfile:
    telegram_id: int
    username: str | None
    gender: Gender | None
    age: int | None
    subscription_expires_at: int | None
    is_banned: bool

    @property
    def is_complete(self) -> bool:
        return self.gender is not None and self.age is not None


@dataclass(slots=True, frozen=True)
class Conversation:
    id: int
    user1_id: int
    user2_id: int
    started_at: int
    ends_at: int | None

    def partner_of(self, user_id: int) -> int:
        if user_id == self.user1_id:
            return self.user2_id
        if user_id == self.user2_id:
            return self.user1_id
        raise ValueError("Пользователь не участвует в этом диалоге")


@dataclass(slots=True, frozen=True)
class MatchResult:
    status: str
    conversation: Conversation | None = None
    user_age: int | None = None
    partner_age: int | None = None


@dataclass(slots=True, frozen=True)
class EndedConversation:
    conversation_id: int
    actor_id: int
    partner_id: int
