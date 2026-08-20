import tempfile
import unittest
from pathlib import Path

from bot.db import Database
from bot.domain import Gender


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.db")
        await self.db.connect()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.temp_dir.cleanup()

    async def add_user(self, user_id: int, gender: Gender, age: int) -> None:
        await self.db.touch_user(user_id, f"user{user_id}")
        await self.db.save_profile(user_id, gender, age)

    async def test_opposite_genders_are_matched(self) -> None:
        await self.add_user(1, Gender.MALE, 23)
        await self.add_user(2, Gender.FEMALE, 21)

        waiting = await self.db.join_or_match(1, 900, now=1_000)
        matched = await self.db.join_or_match(2, 900, now=1_001)

        self.assertEqual(waiting.status, "waiting")
        self.assertEqual(matched.status, "matched")
        self.assertEqual(matched.partner_age, 23)
        self.assertEqual(matched.conversation.ends_at, 1_901)
        self.assertEqual(matched.conversation.partner_of(2), 1)

    async def test_same_gender_stays_in_queue(self) -> None:
        await self.add_user(1, Gender.MALE, 23)
        await self.add_user(2, Gender.MALE, 25)

        first = await self.db.join_or_match(1, 900, now=1_000)
        second = await self.db.join_or_match(2, 900, now=1_001)

        self.assertEqual(first.status, "waiting")
        self.assertEqual(second.status, "waiting")
        self.assertTrue(await self.db.is_waiting(1))
        self.assertTrue(await self.db.is_waiting(2))

    async def test_one_subscription_removes_timer_for_pair(self) -> None:
        await self.add_user(1, Gender.MALE, 30)
        await self.add_user(2, Gender.FEMALE, 29)
        created = await self.db.record_payment(
            user_id=1,
            telegram_charge_id="charge-1",
            amount=199,
            currency="XTR",
            subscription_expires_at=5_000,
            is_recurring=True,
            is_first_recurring=True,
        )

        await self.db.join_or_match(1, 900, now=1_000)
        matched = await self.db.join_or_match(2, 900, now=1_001)

        self.assertTrue(created)
        self.assertEqual(matched.status, "matched")
        self.assertIsNone(matched.conversation.ends_at)

    async def test_expired_free_chat_requeues_both_without_immediate_rematch(self) -> None:
        await self.add_user(1, Gender.MALE, 22)
        await self.add_user(2, Gender.FEMALE, 20)
        await self.db.join_or_match(1, 10, now=100)
        await self.db.join_or_match(2, 10, now=101)

        expired = await self.db.expire_due(now=112)

        self.assertEqual(len(expired), 1)
        self.assertTrue(await self.db.is_waiting(1))
        self.assertTrue(await self.db.is_waiting(2))
        result = await self.db.join_or_match(1, 10, now=113)
        self.assertEqual(result.status, "waiting")

    async def test_report_blocks_future_pair(self) -> None:
        await self.add_user(1, Gender.MALE, 22)
        await self.add_user(2, Gender.FEMALE, 20)
        await self.add_user(3, Gender.FEMALE, 24)
        await self.db.join_or_match(1, 900, now=100)
        matched = await self.db.join_or_match(2, 900, now=101)
        await self.db.add_report_and_block(1, 2, matched.conversation.id)
        await self.db.end_active_conversation(
            1,
            "report",
            requeue_actor=True,
            requeue_partner=True,
            now=102,
        )

        result = await self.db.join_or_match(3, 900, now=103)

        self.assertEqual(result.status, "matched")
        self.assertEqual(result.conversation.partner_of(3), 1)

    async def test_duplicate_payment_is_idempotent(self) -> None:
        await self.add_user(1, Gender.MALE, 22)
        payment = dict(
            user_id=1,
            telegram_charge_id="same-charge",
            amount=199,
            currency="XTR",
            subscription_expires_at=5_000,
            is_recurring=True,
            is_first_recurring=True,
        )

        first = await self.db.record_payment(**payment)
        second = await self.db.record_payment(**payment)

        self.assertTrue(first)
        self.assertFalse(second)

    async def test_free_timer_returns_after_subscription_expires(self) -> None:
        await self.add_user(1, Gender.MALE, 22)
        await self.add_user(2, Gender.FEMALE, 20)
        await self.db.record_payment(
            user_id=1,
            telegram_charge_id="temporary-subscription",
            amount=199,
            currency="XTR",
            subscription_expires_at=200,
            is_recurring=True,
            is_first_recurring=True,
        )
        await self.db.join_or_match(1, 15, now=100)
        matched = await self.db.join_or_match(2, 15, now=101)
        self.assertIsNone(matched.conversation.ends_at)

        changed = await self.db.restore_free_timers(15, now=201)

        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0].ends_at, 216)
        active = await self.db.get_active_conversation(1)
        self.assertEqual(active.ends_at, 216)


if __name__ == "__main__":
    unittest.main()
