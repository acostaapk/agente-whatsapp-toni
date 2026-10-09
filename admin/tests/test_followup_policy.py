import unittest
from pathlib import Path

from admin.followup_policy import FOLLOWUP_MINUTES, purchase_check_due


class FollowupPolicyTests(unittest.TestCase):
    def test_purchase_check_waits_30_minutes(self):
        offer = 1_000_000
        self.assertFalse(
            purchase_check_due(
                offer_ts=offer,
                latest_customer_ts=offer - 10,
                now_ts=offer + 29 * 60,
                already_purchased=False,
                already_sent=False,
            )
        )
        self.assertTrue(
            purchase_check_due(
                offer_ts=offer,
                latest_customer_ts=offer - 10,
                now_ts=offer + 30 * 60,
                already_purchased=False,
                already_sent=False,
            )
        )

    def test_purchase_check_is_suppressed_after_purchase_or_recent_customer_reply(self):
        offer = 1_000_000
        self.assertFalse(
            purchase_check_due(
                offer_ts=offer,
                latest_customer_ts=offer + 5 * 60,
                now_ts=offer + 40 * 60,
                already_purchased=False,
                already_sent=False,
            )
        )

    def test_purchase_check_requires_unsent_offer_and_stays_inside_meta_window(self):
        offer = 1_000_000
        common = {
            "offer_ts": offer,
            "latest_customer_ts": offer - 10,
            "already_purchased": False,
        }
        self.assertFalse(
            purchase_check_due(
                **common,
                now_ts=offer + 30 * 60,
                already_sent=True,
            )
        )
        self.assertFalse(
            purchase_check_due(
                offer_ts=offer,
                latest_customer_ts=offer - 24 * 60 * 60,
                now_ts=offer + 30 * 60,
                already_purchased=False,
                already_sent=False,
            )
        )
        self.assertFalse(
            purchase_check_due(
                offer_ts=offer,
                latest_customer_ts=offer + 1,
                now_ts=offer + 30 * 60,
                already_purchased=False,
                already_sent=False,
            )
        )
        self.assertFalse(
            purchase_check_due(
                offer_ts=offer,
                latest_customer_ts=offer - 10,
                now_ts=offer + 40 * 60,
                already_purchased=True,
                already_sent=False,
            )
        )

    def test_configured_followup_delay_is_30_minutes(self):
        self.assertEqual(FOLLOWUP_MINUTES, 30)

    def test_prompt_does_not_proactively_offer_tapton(self):
        prompt = (Path(__file__).resolve().parents[1] / "prompts" / "toni-system.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Não ofereça TapTon espontaneamente", prompt)


if __name__ == "__main__":
    unittest.main()
