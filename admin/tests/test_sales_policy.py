import unittest

from admin.sales_policy import (
    recommend_for_existing_machine,
    recommend_model,
    same_cnpj_ton_restricted,
)


class RecommendModelTests(unittest.TestCase):
    def test_returns_an_explicitly_requested_available_model(self):
        self.assertEqual(
            recommend_model(
                explicit_model="t3",
                needs_print=False,
                wants_touchscreen_or_4g=False,
                needs_pix=False,
                lowest_investment=False,
                catalog_ids={"t1", "t2", "t3", "t3_smart"},
            ),
            "t3",
        )

    def test_explicit_t2_with_print_requirement_prefers_compatible_t3(self):
        self.assertEqual(
            recommend_model(
                explicit_model="t2",
                needs_print=True,
                wants_touchscreen_or_4g=False,
                needs_pix=False,
                lowest_investment=False,
                catalog_ids={"t1", "t2", "t3", "t3_smart"},
            ),
            "t3",
        )

    def test_explicit_t2_with_print_requirement_returns_none_without_print_model(self):
        self.assertIsNone(
            recommend_model(
                explicit_model="t2",
                needs_print=True,
                wants_touchscreen_or_4g=False,
                needs_pix=False,
                lowest_investment=False,
                catalog_ids={"t2"},
            )
        )

    def test_explicit_t2_with_touchscreen_need_prefers_compatible_smart_model(self):
        self.assertEqual(
            recommend_model(
                explicit_model="t2",
                needs_print=False,
                wants_touchscreen_or_4g=True,
                needs_pix=False,
                lowest_investment=False,
                catalog_ids={"t2", "t3_smart"},
            ),
            "t3_smart",
        )

    def test_explicit_t2_with_touchscreen_need_returns_none_without_smart_model(self):
        self.assertIsNone(
            recommend_model(
                explicit_model="t2",
                needs_print=False,
                wants_touchscreen_or_4g=True,
                needs_pix=False,
                lowest_investment=False,
                catalog_ids={"t2"},
            )
        )

    def test_explicit_t1_without_pix_returns_t1(self):
        self.assertEqual(
            recommend_model(
                explicit_model="t1",
                needs_print=None,
                wants_touchscreen_or_4g=False,
                needs_pix=False,
                lowest_investment=False,
                catalog_ids={"t1", "t2"},
            ),
            "t1",
        )

    def test_explicit_t1_with_pix_returns_t2_when_available(self):
        self.assertEqual(
            recommend_model(
                explicit_model="t1",
                needs_print=None,
                wants_touchscreen_or_4g=False,
                needs_pix=True,
                lowest_investment=False,
                catalog_ids={"t1", "t2"},
            ),
            "t2",
        )

    def test_explicit_t1_with_pix_returns_none_when_t2_is_unavailable(self):
        self.assertIsNone(
            recommend_model(
                explicit_model="t1",
                needs_print=None,
                wants_touchscreen_or_4g=False,
                needs_pix=True,
                lowest_investment=False,
                catalog_ids={"t1"},
            )
        )

    def test_print_without_touchscreen_or_4g_returns_t3(self):
        self.assertEqual(
            recommend_model(
                explicit_model=None,
                needs_print=True,
                wants_touchscreen_or_4g=False,
                needs_pix=False,
                lowest_investment=False,
                catalog_ids={"t2", "t3", "t3_smart"},
            ),
            "t3",
        )

    def test_print_with_touchscreen_or_4g_returns_t3_smart(self):
        self.assertEqual(
            recommend_model(
                explicit_model=None,
                needs_print=True,
                wants_touchscreen_or_4g=True,
                needs_pix=False,
                lowest_investment=False,
                catalog_ids={"t2", "t3", "t3_smart"},
            ),
            "t3_smart",
        )

    def test_digital_receipt_with_pix_returns_t2(self):
        self.assertEqual(
            recommend_model(
                explicit_model=None,
                needs_print=False,
                wants_touchscreen_or_4g=False,
                needs_pix=True,
                lowest_investment=False,
                catalog_ids={"t2", "t3"},
            ),
            "t2",
        )

    def test_lowest_investment_without_pix_returns_t1(self):
        self.assertEqual(
            recommend_model(
                explicit_model=None,
                needs_print=False,
                wants_touchscreen_or_4g=False,
                needs_pix=False,
                lowest_investment=True,
                catalog_ids={"t1", "t2"},
            ),
            "t1",
        )

    def test_returns_none_when_catalog_has_no_compatible_model(self):
        self.assertIsNone(
            recommend_model(
                explicit_model=None,
                needs_print=True,
                wants_touchscreen_or_4g=False,
                needs_pix=False,
                lowest_investment=False,
                catalog_ids={"t1", "t2"},
            )
        )


class SameCnpjTonRestrictedTests(unittest.TestCase):
    def test_active_ton_restricts_only_selected_cnpj(self):
        self.assertTrue(
            same_cnpj_ton_restricted(selected_document="CNPJ", has_active_ton=True)
        )
        self.assertFalse(
            same_cnpj_ton_restricted(selected_document="CPF", has_active_ton=True)
        )

    def test_cnpj_without_active_ton_is_not_restricted(self):
        self.assertFalse(
            same_cnpj_ton_restricted(selected_document="cnpj", has_active_ton=False)
        )


class ExistingMachineRecommendationTests(unittest.TestCase):
    def test_explicit_pix_need_resumes_to_t2(self):
        self.assertEqual(
            recommend_for_existing_machine(
                needs_print=None,
                wants_touchscreen_or_4g=False,
                needs_pix=True,
                lowest_investment=False,
                catalog_ids={"t1", "t2", "t3", "t3_smart"},
            ),
            "t2",
        )

    def test_unknown_needs_do_not_assume_a_model(self):
        self.assertIsNone(
            recommend_for_existing_machine(
                needs_print=None,
                wants_touchscreen_or_4g=False,
                needs_pix=False,
                lowest_investment=False,
                catalog_ids={"t1", "t2", "t3", "t3_smart"},
            )
        )


if __name__ == "__main__":
    unittest.main()
