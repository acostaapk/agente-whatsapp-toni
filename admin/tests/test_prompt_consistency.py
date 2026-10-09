import contextlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = ROOT / "admin" / "prompts" / "toni-system.md"
EXAMPLE_PATH = ROOT / "admin" / "config" / "agent-config.example.json"


def _has_active_two_hour_followup(text: str) -> bool:
    two_hours = re.compile(r"(?i)\b(?:2\s*(?:h|horas?)|duas\s+horas)\b")
    followup = re.compile(r"(?i)\b(?:reengaj\w*|follow[ -]?up|lembrete|sem resposta|inatividade|silêncio)\b")
    negation = re.compile(r"(?i)\b(?:não|nunca|proibido|desativad[oa]s?)\b[^.!?\n]{0,60}$")
    negation_after = re.compile(r"(?i)^[^.!?\n]{0,40}\b(?:proibido|desativad[oa]s?|desligado)\b")
    for match in two_hours.finditer(text):
        start, end = match.span()
        before = text[max(0, start - 100):start]
        after = text[end:min(len(text), end + 100)]
        if not followup.search(before + match.group(0) + after):
            continue
        if negation.search(before) or negation_after.search(after):
            continue
        return True
    return False


def _has_fixed_discount_or_cashback_claim(text: str) -> bool:
    claim = re.compile(
        r"(?i)\b\d+(?:[.,]\d+)?\s*%\s*(?:de\s*)?(?:desconto|cashback)\b"
        r"|\b(?:desconto|cashback)\b\s+(?:de\s+)?\d+(?:[.,]\d+)?\s*%"
        r"|\b(?:desconto|cashback)\b[^.!?\n]{0,50}\b(?:fix[oa]|garantid[oa]s?|autom[aá]tic[oa]s?)\b"
        r"|\b(?:garanto|garantimos|garantir)\b[^.!?\n]{0,50}\b(?:desconto|cashback)\b"
    )
    for sentence in re.split(r"(?<=[.!?\n])\s*", text):
        for match in claim.finditer(sentence):
            context = sentence[max(0, match.start() - 50):min(len(sentence), match.end() + 40)]
            if re.search(r"(?i)\b(?:não|nunca|sem|evite|proibido)\b", context):
                continue
            return True
    return False


def _rewrite_json_fields(path: Path, updates: dict) -> dict:
    """Exercise a parse/serialize update that is restricted to named fields."""
    allowed = {"base_conhecimento", "system_message", "tempo_reengajamento"}
    if not set(updates) <= allowed:
        raise ValueError("Only approved commercial fields can be updated")

    before = json.loads(path.read_text(encoding="utf-8"))
    after = dict(before)
    after.update(updates)
    path.write_text(json.dumps(after, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    written = json.loads(path.read_text(encoding="utf-8"))
    preserved = all(written[key] == value for key, value in before.items() if key not in updates)
    return {"changed_fields": sorted(updates), "other_fields_preserved": preserved}


class PromptConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prompt = PROMPT_PATH.read_text(encoding="utf-8")
        cls.example = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
        cls.base = cls.example["base_conhecimento"]
        cls.fallback = cls.example["system_message"]

    def test_prompt_has_one_30_minute_followup_and_no_active_legacy_timing(self):
        section = re.search(r"(?ms)^# REENGAJAMENTO\s*$([\s\S]*?)(?=^# |\Z)", self.prompt)
        self.assertIsNotNone(section, "The prompt must have one authoritative follow-up section")
        body = section.group(1)
        self.assertRegex(body, r"30 minutos")
        self.assertNotRegex(body, r"(?i)(?:depois|após|aproximadamente)\s*(?:de\s*)?2\s*horas")
        for line in (body + "\n" + self.fallback).splitlines():
            if re.search(r"(?i)\b(?:enviar|envie|mande|dispare)\b", line) and re.search(r"(?i)\b(?:24\s*h|72\s*h)\b", line):
                self.assertRegex(line, r"(?i)\b(?:não|nunca|desativad|proib)")
        self.assertEqual(self.example.get("tempo_reengajamento"), "30 minutos")

    def test_no_active_two_hour_followup_anywhere_in_prompt_or_fallback(self):
        active_texts = [self.prompt, self.fallback, self.base]
        runtime_path = ROOT / "admin" / "config" / "agent-config.json"
        if runtime_path.exists():
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            active_texts.extend((runtime.get("system_message", ""), runtime.get("base_conhecimento", "")))
        self.assertTrue(_has_active_two_hour_followup("Reengajar após 2 horas sem resposta."))
        self.assertFalse(_has_active_two_hour_followup("Não reengajar após 2 horas; o lembrete fica desativado."))
        for text in active_texts:
            self.assertFalse(_has_active_two_hour_followup(text), "Found an active 2-hour follow-up instruction")

    def test_prompt_consolidates_existing_machine_tapton_and_photo_behavior(self):
        self.assertRegex(self.prompt, r"(?is)já tem(?: uma)? maquininha.{0,250}motivo")
        self.assertNotRegex(
            self.prompt,
            r"(?is)# OBJEÇÃO \"JÁ TENHO MAQUININHA\".*?T2 por R\$",
        )
        self.assertRegex(self.prompt, r"(?i)não ofereça TapTon espontaneamente")
        self.assertRegex(self.fallback, r"(?i)não ofereça TapTon espontaneamente")
        self.assertNotRegex(
            self.prompt,
            r"(?is)(?:sistema envia|envia sozinho|link.{0,40}automaticamente).{0,100}foto|foto.{0,100}(?:automaticamente|sozinho)",
        )
        self.assertNotRegex(self.fallback, r"(?is)foto.{0,100}(?:automaticamente|sozinho)")

    def test_prompt_answers_hot_leads_before_qualification_and_defers_os_until_purchase(self):
        self.assertRegex(self.prompt, r"(?is)Lead quente.*?responda primeiro.*?(?:preço|link).{0,250}(?:não faça|nem pergunte|sem perguntar).{0,40}(?:Android|iPhone)")
        self.assertRegex(
            self.prompt,
            r"(?is)# COMPRA DECLARADA E LINK DO APP.*?Somente após.*?pergunte se usa Android ou iPhone.*?envie o link de app",
        )
        self.assertNotRegex(self.prompt, r"(?is)(?:antes da compra|pré-compra).{0,80}(?:Android|iPhone)|se não souber.{0,60}(?:Android|iPhone).{0,60}pergunte")
        self.assertRegex(self.prompt, r"(?is)resposta determinística.{0,150}(?:não|nunca).{0,80}(?:segunda oferta|segundo CTA|pergunta adicional)")

    def test_prompt_has_one_qualified_support_and_optout_policy(self):
        self.assertRegex(self.prompt, r"(?is)# SUPORTE E OPT-OUT.*?pós-venda.*?Ton.*?opt-out.*?(?:bloque|suprim)")
        self.assertNotRegex(self.prompt, r"(?is)# SUPORTE\s*$[\s\S]*?(?=^# |\Z)[\s\S]*?\n# SUPORTE")

    def test_example_has_dated_approved_catalog_and_qualified_commercial_facts(self):
        for fact in ("07/10/2026", "Ton Mega+", "Ton Black", "T1", "T2", "T3 Smart", "R$16,80", "R$49,88", "R$108,00", "R$191,88"):
            with self.subTest(fact=fact):
                self.assertIn(fact, self.base)
        self.assertRegex(self.base, r"(?is)a partir de 0,74%.*?(?:confirme|consult).{0,80}app")
        self.assertRegex(self.base, r"(?is)recebimento.{0,100}(?:condiç|modalidade|opç|app)")
        self.assertRegex(self.base, r"(?is)comprovante.{0,100}(?:SMS|impresso)")
        self.assertRegex(self.base, r"(?i)garantia vitalícia")
        self.assertRegex(self.base, r"(?is)Pix.{0,200}chave.{0,100}0,49%")
        self.assertRegex(self.base, r"(?is)frete grátis.{0,100}3 a 7 dias úteis")
        self.assertRegex(self.base, r"(?is)1 ou 2 dias.{0,100}(?:não é prazo oficial|não.{0,25}garantia)")
        self.assertNotRegex(self.base, r"(?i)0,57%|3,97%|6,97%|7,97%|23/09/2026|06/10/2026")
        self.assertNotRegex(self.base, r"(?i)(?:só hoje|últimos dias|oferta da semana|estoque limitado)")
        self.assertEqual(len(self.example["modelos"]), 4)
        for model in self.example["modelos"]:
            with self.subTest(model=model.get("id")):
                self.assertTrue(str(model.get("preco", "")).strip(), "Every model needs its current price")
                self.assertTrue(str(model.get("parcelas", "")).strip(), "Every model needs its installment terms")

    def test_model_promotion_snapshot_matches_owner_observation(self):
        expected = {
            "t1": {"desconto": "70% OFF", "cupom": None, "preco_observado": "R$ 16,80", "observado_em": "07/10/2026"},
            "t2": {"desconto": "72% OFF", "cupom": "20%", "preco_observado": "R$ 39,90", "observado_em": "07/10/2026"},
            "t3": {"desconto": "72% OFF", "cupom": "20%", "preco_observado": "R$ 86,40", "observado_em": "07/10/2026"},
            "t3_smart": {"desconto": "71% OFF", "cupom": "20%", "preco_observado": "R$ 153,50", "observado_em": "07/10/2026"},
        }
        expected_catalog = {
            "t1": ("R$ 16,80", "em até 12x"),
            "t2": ("R$ 49,88", "em até 12x"),
            "t3": ("R$ 108,00", "em até 21x"),
            "t3_smart": ("R$ 191,88", "em até 21x"),
        }
        models = {model["id"]: model for model in self.example["modelos"]}
        self.assertEqual(set(models), set(expected))
        for model_id, snapshot in expected.items():
            with self.subTest(model=model_id):
                self.assertEqual(models[model_id].get("promocao"), snapshot)
                self.assertEqual((models[model_id].get("preco"), models[model_id].get("parcelas")), expected_catalog[model_id])
                self.assertTrue(models[model_id].get("link"))

        marker = "Snapshot promocional afiliado observado em "
        self.assertIn(marker, self.base)
        section = self.base.split(marker, 1)[1].split("\n\n", 1)[0]
        self.assertIn("07/10/2026", section)
        self.assertIn("dinâmico", section.lower())
        self.assertRegex(section.lower(), r"carrinho.*(?:final|confirme)|(?:final|confirme).*carrinho")
        self.assertNotIn("cashback", section.lower())
        for model_id, snapshot in expected.items():
            with self.subTest(base_model=model_id):
                self.assertIn(snapshot["desconto"], section)
                self.assertIn(snapshot["preco_observado"], section)
                self.assertIn("sem cupom extra" if snapshot["cupom"] is None else "cupom 20%", section)
        self.assertIn("snapshot correspondente da `{{base_conhecimento}}`", self.prompt)
        self.assertIn("os campos `promocao`", self.prompt)
        self.assertIn("T1 não tem cupom extra", self.prompt)
        self.assertIn("Nunca chame a promoção de cashback", self.prompt)

    def test_example_has_black_links_only_for_eligible_t3_plans(self):
        models = {model["id"]: model for model in self.example["modelos"]}
        self.assertNotIn("link_black", models["t1"])
        self.assertNotIn("link_black", models["t2"])

        expected_products = {
            "t3": "TONBLACK_TIER_S920",
            "t3_smart": "TONBLACK_TIER_SMART_POS",
        }
        for model_id, product_id in expected_products.items():
            with self.subTest(model=model_id):
                black_link = models[model_id].get("link_black", "")
                self.assertTrue(black_link)
                black_query = parse_qs(urlparse(black_link).query)
                mega_query = parse_qs(urlparse(models[model_id]["link"]).query)
                self.assertEqual(black_query.get("productId"), [product_id])
                self.assertEqual(black_query.get("referrer"), mega_query.get("referrer"))
                self.assertTrue(black_query.get("referrer", [""])[0])

    def test_promotional_claim_guard_is_general_and_allows_conditional_rate(self):
        self.assertTrue(_has_fixed_discount_or_cashback_claim("Garantimos 5% de desconto na adesão."))
        self.assertTrue(_has_fixed_discount_or_cashback_claim("Desconto de 10% na adesão."))
        self.assertTrue(_has_fixed_discount_or_cashback_claim("Cashback de 5%."))
        self.assertTrue(_has_fixed_discount_or_cashback_claim("O desconto fixo é de 10%."))
        self.assertTrue(_has_fixed_discount_or_cashback_claim("Você recebe cashback garantido."))
        self.assertFalse(_has_fixed_discount_or_cashback_claim("Não garantimos 5% de desconto."))
        self.assertFalse(_has_fixed_discount_or_cashback_claim("Não oferecemos desconto de 10%."))
        self.assertFalse(
            _has_fixed_discount_or_cashback_claim(
                "Taxas a partir de 0,74% no débito, conforme plano e conta; confirme no app."
            )
        )
        for text in (self.prompt, self.fallback, self.base):
            self.assertFalse(_has_fixed_discount_or_cashback_claim(text))

    def test_discount_wording_and_affiliate_checkout_links_are_unchanged(self):
        self.assertIn(
            "Desconto: quem pede pelo nosso link tem a possibilidade de desconto na adesão (não é garantia de valor), sem custo extra.",
            self.base,
        )
        expected_links = {
            "t1": "https://www.ton.com.br/checkout/cart?userTag=tonmega_tier&userAnticipation=1&productId=TONMEGA_TIER_D150&referrer=B7C09243-0F6C-4FA5-BA01-4562B9C88FD6&utm_medium=invite_share&utm_source=revendedor",
            "t2": "https://www.ton.com.br/checkout/cart?userTag=tonmega_tier&userAnticipation=1&productId=TONMEGA_TIER_D195&referrer=B7C09243-0F6C-4FA5-BA01-4562B9C88FD6&utm_medium=invite_share&utm_source=revendedor",
            "t3": "https://www.ton.com.br/checkout/cart?userTag=tonmega_tier&userAnticipation=1&productId=TONMEGA_TIER_S920&referrer=B7C09243-0F6C-4FA5-BA01-4562B9C88FD6&utm_medium=invite_share&utm_source=revendedor",
            "t3_smart": "https://www.ton.com.br/checkout/cart?userTag=tonmega_tier&userAnticipation=1&productId=TONMEGA_TIER_SMART_POS&referrer=B7C09243-0F6C-4FA5-BA01-4562B9C88FD6&utm_medium=invite_share&utm_source=revendedor",
        }
        self.assertEqual({item["id"]: item["link"] for item in self.example["modelos"]}, expected_links)

    def test_json_update_preserves_secret_sentinels_and_emits_no_values(self):
        sentinel = "synthetic-secret-sentinel-do-not-print"
        original = {
            "base_conhecimento": "old commercial text",
            "system_message": "old fallback",
            "tempo_reengajamento": "2 horas",
            "meta": {"access_token": sentinel},
            "llm": {"api_key": sentinel},
            "affiliate": {"checkout": "https://example.invalid/?ref=unchanged"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(original), encoding="utf-8")
            emitted = io.StringIO()
            with contextlib.redirect_stdout(emitted), contextlib.redirect_stderr(emitted):
                result = _rewrite_json_fields(
                    path,
                    {
                        "base_conhecimento": "new commercial text",
                        "system_message": "safe fallback",
                        "tempo_reengajamento": "30 minutos",
                    },
                )
            after = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(result["other_fields_preserved"], "Noncommercial values must remain unchanged")
        self.assertEqual(
            result["changed_fields"],
            ["base_conhecimento", "system_message", "tempo_reengajamento"],
        )
        self.assertTrue(after["meta"]["access_token"] == sentinel)
        self.assertTrue(after["llm"]["api_key"] == sentinel)
        self.assertEqual(after["affiliate"], original["affiliate"])
        self.assertNotIn(sentinel, emitted.getvalue())

    def test_runtime_commercial_fields_match_sanitized_example(self):
        runtime_path = ROOT / "admin" / "config" / "agent-config.json"
        if not runtime_path.exists():
            self.skipTest("Runtime config is local and is not present in this checkout")
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        self.assertTrue(runtime.get("base_conhecimento") == self.base)
        self.assertTrue(runtime.get("system_message") == self.fallback)
        self.assertEqual(runtime.get("tempo_reengajamento"), "30 minutos")
        self.assertNotRegex(runtime["base_conhecimento"], r"(?i)0,57%|3,97%|6,97%|7,97%|23/09/2026|06/10/2026")


if __name__ == "__main__":
    unittest.main()
