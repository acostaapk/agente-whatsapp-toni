import io
import json
import os
import re
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


MODEL_PROMO_EXPECTED = {
    "t1": {
        "nome": "T1", "desconto": "70% OFF", "cupom": None,
        "preco_observado": "R$ 16,80", "observado_em": "07/10/2026", "link_slug": "t1",
    },
    "t2": {
        "nome": "T2", "desconto": "72% OFF", "cupom": "20%",
        "preco_observado": "R$ 39,90", "observado_em": "07/10/2026", "link_slug": "t2",
    },
    "t3": {
        "nome": "T3", "desconto": "72% OFF", "cupom": "20%",
        "preco_observado": "R$ 86,40", "observado_em": "07/10/2026", "link_slug": "t3",
    },
    "t3_smart": {
        "nome": "T3 Smart", "desconto": "71% OFF", "cupom": "20%",
        "preco_observado": "R$ 153,50", "observado_em": "07/10/2026", "link_slug": "t3-smart",
    },
}


class WhatsAppDryRunTests(unittest.TestCase):
    @staticmethod
    def _checkout_test_models():
        return [
            {
                "id": "t1",
                "nome": "T1",
                "preco": "R$ 16,80",
                "parcelas": "em até 12x",
                "promocao": {
                    "desconto": "70% OFF", "cupom": None,
                    "preco_observado": "R$ 16,80", "observado_em": "07/10/2026",
                },
                "link": "https://checkout.example.invalid/mega-plus/t1",
                "link_black": "https://checkout.example.invalid/black/t1",
            },
            {
                "id": "t2",
                "nome": "T2",
                "preco": "R$ 49,88",
                "parcelas": "em até 12x",
                "promocao": {
                    "desconto": "72% OFF", "cupom": "20%",
                    "preco_observado": "R$ 39,90", "observado_em": "07/10/2026",
                },
                "link": "https://checkout.example.invalid/mega-plus/t2",
                "link_black": "https://checkout.example.invalid/black/t2",
            },
            {
                "id": "t3",
                "nome": "T3",
                "preco": "R$ 108,00",
                "parcelas": "em até 21x",
                "promocao": {
                    "desconto": "72% OFF", "cupom": "20%",
                    "preco_observado": "R$ 86,40", "observado_em": "07/10/2026",
                },
                "link": "https://checkout.example.invalid/mega-plus/t3",
                "link_black": "https://checkout.example.invalid/black/t3",
                "foto": "https://images.example.invalid/t3.jpg",
            },
            {
                "id": "t3_smart",
                "nome": "T3 Smart",
                "preco": "R$ 191,88",
                "parcelas": "em até 21x",
                "promocao": {
                    "desconto": "71% OFF", "cupom": "20%",
                    "preco_observado": "R$ 153,50", "observado_em": "07/10/2026",
                },
                "link": "https://checkout.example.invalid/mega-plus/t3-smart",
                "link_black": "https://checkout.example.invalid/black/t3-smart",
            },
        ]

    def _run_button(
        self, button_id, *, sender, initial_qualify, reset_state=True, models=None
    ):
        from admin.tests.whatsapp_test_harness import (
            FIXTURE_PATH,
            FakeRequest,
            WhatsAppDryRun,
            _load_main,
            run_webhook,
        )

        main = _load_main()
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        fixture["modelos"] = models if models is not None else self._checkout_test_models()
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": sender,
                            "id": f"wamid.{button_id}",
                            "type": "interactive",
                            "interactive": {"button_reply": {"id": button_id}},
                        }],
                    },
                }],
            }],
        }

        original_read_text = Path.read_text

        def read_synthetic_config(path, *args, **kwargs):
            if path.resolve() == FIXTURE_PATH.resolve():
                return json.dumps(fixture)
            return original_read_text(path, *args, **kwargs)

        dry_run = WhatsAppDryRun()
        with patch.object(Path, "read_text", read_synthetic_config):
            result = run_webhook(
                FakeRequest(payload),
                dry_run,
                initial_qualify={sender: initial_qualify},
                reset_state=reset_state,
            )
        return result, dry_run, main._q_get(sender), main

    def _run_text(
        self,
        text,
        *,
        sender,
        initial_qualify=None,
        empty_model_prices=False,
        llm_reply=None,
        reset_state=True,
        thread_runner=None,
        canal=None,
        models=None,
        base_conhecimento=None,
    ):
        from admin.tests.whatsapp_test_harness import (
            FIXTURE_PATH,
            FakeRequest,
            WhatsAppDryRun,
            _load_main,
            run_webhook,
        )

        main = _load_main()
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        fixture["modelos"] = models if models is not None else self._checkout_test_models()
        if base_conhecimento is not None:
            fixture["base_conhecimento"] = base_conhecimento
        if canal is not None:
            fixture["canal"] = canal
        if empty_model_prices:
            for modelo in fixture["modelos"]:
                modelo["preco"] = ""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": sender,
                            "id": f"wamid.text-{sender}-{len(text)}-{text[:12]}",
                            "type": "text",
                            "text": {"body": text},
                        }],
                    },
                }],
            }],
        }

        original_read_text = Path.read_text

        def read_synthetic_config(path, *args, **kwargs):
            if path.resolve() == FIXTURE_PATH.resolve():
                return json.dumps(fixture)
            return original_read_text(path, *args, **kwargs)

        dry_run = WhatsAppDryRun()
        http_response = None
        if llm_reply is not None:
            http_response = {
                "choices": [{"message": {"content": llm_reply}}],
            }
        with patch.object(Path, "read_text", read_synthetic_config):
            result = run_webhook(
                FakeRequest(payload),
                dry_run,
                initial_qualify={sender: initial_qualify},
                reset_state=reset_state,
                thread_runner=thread_runner,
                http_response=http_response,
            )
        return result, dry_run, main._q_get(sender), main

    def _dispatch_reply_dry_run(self, qualify_state):
        from admin.tests.whatsapp_test_harness import (
            FIXTURE_PATH,
            WhatsAppDryRun,
            _load_main,
        )

        main = _load_main()
        cfg = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        modelo = {
            "id": "t3",
            "nome": "T3",
            "foto": "https://images.example.invalid/t3.jpg",
            "link": "https://checkout.example.invalid/mega-plus/t3",
        }
        cfg["canal"] = "baileys"
        cfg["modelos"] = [modelo]
        sender = "15555550500"
        dry_run = WhatsAppDryRun()
        media_calls = []
        with (
            patch.object(main, "_q_get", return_value=qualify_state),
            patch.object(
                main,
                "_send_reply",
                side_effect=lambda cfg, to, text: dry_run.send_text(to, text),
            ),
            patch.object(
                main,
                "_send_media",
                side_effect=lambda *args, **kwargs: media_calls.append((args, kwargs)),
            ),
            patch.object(main, "_pausa", return_value=None),
        ):
            main._dispatch_reply(
                cfg,
                sender,
                "",
                "",
                "Resposta útil para a dúvida.",
                "baileys",
                modelo,
                False,
                [],
            )
        return dry_run, media_calls

    def _deliver_offer_dry_run(self):
        from admin.tests.whatsapp_test_harness import FIXTURE_PATH, _load_main

        main = _load_main()
        cfg = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        modelo = {
            "id": "t3",
            "nome": "T3",
            "foto": "https://images.example.invalid/t3.jpg",
            "link": "https://checkout.example.invalid/mega-plus/t3",
        }
        cfg["canal"] = "baileys"
        cfg["modelos"] = [modelo]
        sent_text = []
        media_calls = []
        with (
            patch.object(main, "_guard_ton_checkout", return_value=None),
            patch.object(main, "_q_set"),
            patch.object(main, "_rec_package", return_value="Oferta textual da T3."),
            patch.object(main, "_ensure_pix_note", side_effect=lambda text, _modelo: text),
            patch.object(
                main, "_apply_model_link",
                side_effect=lambda text, _modelo, _link=None: text,
            ),
            patch.object(main, "_send_reply", side_effect=lambda _cfg, _to, text: sent_text.append(text)),
            patch.object(main, "_send_media", side_effect=lambda *a, **kw: media_calls.append((a, kw))),
            patch.object(main, "_send_voice_or_text"),
            patch.object(main, "_already_offered", return_value=False),
            patch.object(main, "_history_append"),
            patch.object(main, "_lead_event"),
            patch.object(main, "_log_event"),
        ):
            result = main._deliver_offer(cfg, "15555550501", modelo, {}, [])
        return result, sent_text, media_calls

    @staticmethod
    def _checkout_urls(text):
        return [
            url.rstrip(".,!?;:)]}")
            for url in re.findall(r"https?://checkout\.example\.invalid/[^\s<>]+", text)
        ]

    def _assert_checkout_url_contract(self, visible_text, expected_url):
        expected = (
            [expected_url]
            if expected_url and expected_url.startswith("https://checkout.example.invalid/")
            else []
        )
        self.assertEqual(self._checkout_urls(visible_text), expected)

    def _assert_tapton_scope(self, step, visible_text):
        if step.get("tapton_inquiry"):
            self.assertRegex(step.get("input", ""), r"(?i)\btap\s?ton\b")
        else:
            self.assertNotRegex(visible_text, r"(?i)\btap\s?ton\b")

    def _assert_support_pitch_absent(self, visible_text):
        self.assertEqual(self._checkout_urls(visible_text), [])
        sales_pitch = (
            r"\b(?:compre|adquira)\s+(?:agora|a sua|o seu|um|uma)\b"
            r"|\baproveite\b.{0,50}\b(?:oferta|promo[çc][ãa]o|desconto|compra)\b"
            r"|\bgaranta\b.{0,50}\b(?:oferta|promo[çc][ãa]o|desconto|compra)\b"
            r"|\b(?:quer|posso|vamos)\b.{0,30}\b(?:enviar|mandar|passar)\b.{0,30}\blink\b"
            r"|\b(?:fechar|finalizar)\s+(?:a\s+)?compra\b"
        )
        self.assertNotRegex(visible_text.lower(), sales_pitch)

    def test_cnpj_selection_asks_whether_ton_is_active_before_recommending(self):
        sender = "15555550111"
        result, dry_run, state, _ = self._run_button(
            "cnpj",
            sender=sender,
            initial_qualify={
                "step": "pessoa",
                "tipo": "loja_fisica",
                "comprovante": "impresso",
                "volume": "ate_10k",
            },
        )

        self.assertIsNotNone(result, "the CNPJ Ton button was not handled")
        self.assertEqual(result["status"], "btn_cnpj_ton")
        self.assertIn("Ton", dry_run.buttons[-1][1])
        self.assertEqual(
            [button_id for button_id, _ in dry_run.buttons[-1][2]],
            ["cnpj_tem_ton_sim", "cnpj_tem_ton_nao"],
        )
        self.assertEqual(state["step"], "cnpj_ton")
        self.assertFalse(any("checkout.example.invalid" in text for _, text in dry_run.replies))

    def test_fresh_cnpj_selection_resets_previous_active_ton_flags(self):
        sender = "15555550401"
        result, dry_run, state, _ = self._run_button(
            "cnpj",
            sender=sender,
            initial_qualify={
                "step": "encerrado",
                "pessoa": "cpf",
                "has_active_ton": True,
                "continue_as_cpf": True,
                "modelo": "t3",
                "tipo": "loja_fisica",
                "comprovante": "impresso",
                "volume": "ate_10k",
            },
        )

        self.assertEqual(result["status"], "btn_cnpj_ton")
        self.assertEqual(
            [button_id for button_id, _ in dry_run.buttons[-1][2]],
            ["cnpj_tem_ton_sim", "cnpj_tem_ton_nao"],
        )
        self.assertFalse(state["has_active_ton"])
        self.assertFalse(state["continue_as_cpf"])
        self.assertEqual(state["pessoa"], "cnpj")
        self.assertEqual(state["step"], "cnpj_ton")
        self.assertFalse(any("checkout.example.invalid" in text for _, text in dry_run.replies))

    def test_cnpj_without_active_ton_proceeds_to_recommendation(self):
        sender = "15555550112"
        result, dry_run, state, _ = self._run_button(
            "cnpj_tem_ton_nao",
            sender=sender,
            initial_qualify={
                "step": "cnpj_ton",
                "pessoa": "cnpj",
                "tipo": "loja_fisica",
                "comprovante": "impresso",
                "volume": "ate_10k",
            },
        )

        self.assertIsNotNone(result, "the inactive-Ton button was not handled")
        self.assertEqual(result["status"], "btn_recomendacao")
        self.assertEqual(result["modelo"], "t3")
        self.assertFalse(state["has_active_ton"])
        self.assertTrue(any("T3" in text for _, text in dry_run.replies))

    def test_button_recommendation_treats_prior_explicit_t2_as_preference(self):
        from admin.tests.whatsapp_test_harness import _load_main

        main = _load_main()
        sender = "15555550830"
        main.HISTORY[sender] = [{
            "role": "user",
            "content": "Antes perguntei sobre a T2.",
            "ts": main.time.time(),
        }]
        main.QUALIFY[sender] = {
            "step": "pessoa",
            "tipo": "loja_fisica",
            "comprovante": "impresso",
            "volume": "ate_10k",
        }
        main.SEEN_MSG_IDS.clear()

        result, dry_run, state, _ = self._run_button(
            "cpf",
            sender=sender,
            initial_qualify={},
            reset_state=False,
        )

        response = "\n".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "btn_recomendacao")
        self.assertEqual(result["modelo"], "t3")
        self.assertEqual(state["modelo"], "t3")
        self.assertIn("T2", response)
        self.assertIn("comprovante impresso", response.lower())
        self.assertIn("T3", response)

    def test_button_recommendation_with_only_incompatible_explicit_model_asks_human(self):
        t2_only = [model for model in self._checkout_test_models() if model["id"] == "t2"]
        sender = "15555550831"
        from admin.tests.whatsapp_test_harness import _load_main

        main = _load_main()
        main.HISTORY[sender] = [{
            "role": "user",
            "content": "Quero saber da T2.",
            "ts": main.time.time(),
        }]
        main.QUALIFY[sender] = {
            "step": "pessoa",
            "tipo": "loja_fisica",
            "comprovante": "impresso",
            "volume": "ate_10k",
        }
        main.SEEN_MSG_IDS.clear()

        result, dry_run, state, _ = self._run_button(
            "cpf",
            sender=sender,
            initial_qualify={},
            reset_state=False,
            models=t2_only,
        )

        response = "\n".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "btn_sem_modelo")
        self.assertEqual(state["step"], "atendimento")
        self.assertNotIn("T2", response)
        self.assertNotIn("checkout.example.invalid", response)
        self.assertEqual(
            [label for _, _, buttons in dry_run.buttons for _, label in buttons],
            ["Falar com atendente"],
        )

    def test_partial_catalog_with_print_requirement_does_not_fall_back_to_t2(self):
        t2_only = [model for model in self._checkout_test_models() if model["id"] == "t2"]
        self.assertEqual([model["id"] for model in t2_only], ["t2"])

        for index, document in enumerate(("cpf", "cnpj")):
            with self.subTest(document=document):
                sender = f"1555555082{index}"
                state = {
                    "step": "pessoa",
                    "tipo": "loja_fisica",
                    "comprovante": "impresso",
                    "volume": "ate_10k",
                }
                outputs = []

                if document == "cnpj":
                    gate_result, gate_dry_run, state, _ = self._run_button(
                        "cnpj",
                        sender=sender,
                        initial_qualify=state,
                        models=t2_only,
                    )
                    self.assertEqual(gate_result["status"], "btn_cnpj_ton")
                    outputs.extend(text for _, text in gate_dry_run.replies)
                    state = dict(state)
                    next_button = "cnpj_tem_ton_nao"
                else:
                    next_button = "cpf"

                result, dry_run, state, _ = self._run_button(
                    next_button,
                    sender=sender,
                    initial_qualify=state,
                    reset_state=document != "cnpj",
                    models=t2_only,
                )
                response = "\n".join(text for _, text in dry_run.replies)
                outputs.extend(text for _, text in dry_run.replies)

                self.assertEqual(result["status"], "btn_sem_modelo")
                self.assertEqual(state.get("step"), "atendimento")
                self.assertIn(
                    "Não encontrei um modelo que eu possa confirmar para as necessidades informadas",
                    response,
                )
                self.assertNotRegex(
                    response.lower(),
                    r"comprovante impresso|\bpix\b|\btela\b|\b4g\b|menor investimento",
                )
                self.assertNotRegex(response.lower(), r"\bt2\b|checkout|\blink\b")
                self.assertEqual(
                    [button_id for _, _, buttons in dry_run.buttons for button_id, _ in buttons],
                    ["atendente"],
                )
                self.assertEqual(
                    [label for _, _, buttons in dry_run.buttons for _, label in buttons],
                    ["Falar com atendente"],
                )
                self._assert_checkout_url_contract("\n".join(outputs), None)

    def test_active_ton_offers_cpf_or_stop_without_checkout_or_document_numbers(self):
        sender = "15555550113"
        result, dry_run, state, main = self._run_button(
            "cnpj_tem_ton_sim",
            sender=sender,
            initial_qualify={
                "step": "cnpj_ton",
                "pessoa": "cnpj",
                "tipo": "loja_fisica",
                "comprovante": "impresso",
                "volume": "30k_plus",
            },
        )

        self.assertIsNotNone(result, "the active-Ton button was not handled")
        self.assertEqual(result["status"], "btn_continuar_cpf")
        self.assertEqual(
            [button_id for button_id, _ in dry_run.buttons[-1][2]],
            ["continuar_cpf", "nao_seguir"],
        )
        self.assertTrue(state["has_active_ton"])
        self.assertFalse(state["continue_as_cpf"])
        self.assertFalse(any("checkout.example.invalid" in text for _, text in dry_run.replies))
        self.assertNotRegex(json.dumps(state), r"\b\d{11,14}\b")
        self.assertNotRegex(
            " ".join(message["content"] for message in main._history_get(sender)),
            r"\b\d{11,14}\b",
        )

    def test_active_ton_sends_mega_plus_checkout_only_after_explicit_cpf_continuation(self):
        sender = "15555550114"
        _, _, state, _ = self._run_button(
            "cnpj_tem_ton_sim",
            sender=sender,
            initial_qualify={
                "step": "cnpj_ton",
                "pessoa": "cnpj",
                "tipo": "loja_fisica",
                "comprovante": "impresso",
                "volume": "30k_plus",
            },
        )
        result, dry_run, state, _ = self._run_button(
            "continuar_cpf", sender=sender, initial_qualify=state
        )

        self.assertIsNotNone(result, "the CPF continuation button was not handled")
        self.assertEqual(result["status"], "btn_recomendacao")
        self.assertTrue(state["continue_as_cpf"])
        self.assertEqual(state["pessoa"], "cpf")
        self.assertFalse(any("checkout.example.invalid" in text for _, text in dry_run.replies))

        result, dry_run, _, _ = self._run_button(
            "comprar", sender=sender, initial_qualify=state
        )
        self.assertEqual(result["status"], "btn_comprar")
        self.assertIn(
            "https://checkout.example.invalid/mega-plus/t3",
            " ".join(text for _, text in dry_run.replies),
        )
        self.assertNotIn(
            "https://checkout.example.invalid/black/t3",
            " ".join(text for _, text in dry_run.replies),
        )

    def test_active_ton_stop_choice_sends_no_checkout(self):
        sender = "15555550116"
        result, dry_run, state, _ = self._run_button(
            "nao_seguir",
            sender=sender,
            initial_qualify={
                "step": "continuar_cpf",
                "pessoa": "cnpj",
                "has_active_ton": True,
                "continue_as_cpf": False,
                "modelo": "t3",
                "tipo": "loja_fisica",
                "comprovante": "impresso",
                "volume": "30k_plus",
            },
        )

        self.assertEqual(result["status"], "btn_nao_seguir")
        self.assertEqual(state["step"], "encerrado")
        self.assertFalse(state["continue_as_cpf"])
        self.assertFalse(any("checkout.example.invalid" in text for _, text in dry_run.replies))

    def test_active_cnpj_cannot_send_existing_checkout_without_cpf_choice(self):
        sender = "15555550117"
        result, dry_run, _, _ = self._run_button(
            "comprar",
            sender=sender,
            initial_qualify={
                "step": "continuar_cpf",
                "pessoa": "cnpj",
                "has_active_ton": True,
                "continue_as_cpf": False,
                "modelo": "t3",
                "tipo": "loja_fisica",
                "comprovante": "impresso",
                "volume": "30k_plus",
            },
        )

        self.assertEqual(result["status"], "btn_continuar_cpf")
        self.assertEqual(dry_run.buttons[-1][2], [
            ("continuar_cpf", "Continuar pelo CPF"),
            ("nao_seguir", "Não seguir agora"),
        ])
        self.assertFalse(any("checkout.example.invalid" in text for _, text in dry_run.replies))

    def test_pending_cnpj_ton_answer_cannot_send_an_existing_checkout(self):
        sender = "15555550118"
        result, dry_run, _, _ = self._run_button(
            "comprar",
            sender=sender,
            initial_qualify={
                "step": "cnpj_ton",
                "pessoa": "cnpj",
                "modelo": "t3",
                "tipo": "loja_fisica",
                "comprovante": "impresso",
                "volume": "30k_plus",
            },
        )

        self.assertEqual(result["status"], "btn_cnpj_ton")
        self.assertEqual(dry_run.buttons[-1][2], [
            ("cnpj_tem_ton_sim", "Sim"),
            ("cnpj_tem_ton_nao", "Não"),
        ])
        self.assertFalse(any("checkout.example.invalid" in text for _, text in dry_run.replies))

    def test_cpf_selection_recommends_without_asking_about_active_ton(self):
        sender = "15555550115"
        result, dry_run, _, _ = self._run_button(
            "cpf",
            sender=sender,
            initial_qualify={
                "step": "pessoa",
                "tipo": "loja_fisica",
                "comprovante": "impresso",
                "volume": "ate_10k",
            },
        )

        self.assertEqual(result["status"], "btn_recomendacao")
        self.assertFalse(
            any("cnpj_tem_ton" in button_id for _, _, buttons in dry_run.buttons for button_id, _ in buttons)
        )

    def test_text_hot_paths_respect_pending_or_active_cnpj_ton_gate(self):
        cases = (
            (
                "pending",
                {"step": "cnpj_ton", "pessoa": "cnpj", "modelo": "t3"},
                ["cnpj_tem_ton_sim", "cnpj_tem_ton_nao"],
            ),
            (
                "active",
                {
                    "step": "continuar_cpf",
                    "pessoa": "cnpj",
                    "has_active_ton": True,
                    "continue_as_cpf": False,
                    "modelo": "t3",
                },
                ["continuar_cpf", "nao_seguir"],
            ),
        )
        for status, state, expected_buttons in cases:
            for request in ("Quero comprar a T3", "Me manda o link"):
                with self.subTest(status=status, request=request):
                    sender = f"155555502{len(status)}{len(request)}"
                    result, dry_run, _, _ = self._run_text(
                        request,
                        sender=sender,
                        initial_qualify={**state, "tipo": "loja_fisica", "comprovante": "impresso"},
                    )

                    expected_status = (
                        "btn_cnpj_ton" if status == "pending" else "btn_continuar_cpf"
                    )
                    self.assertEqual(result["status"], expected_status)
                    self.assertEqual(
                        [button_id for button_id, _ in dry_run.buttons[-1][2]],
                        expected_buttons,
                    )
                    self.assertFalse(
                        any("checkout.example.invalid" in text for _, text in dry_run.replies)
                    )

    def test_pending_cnpj_price_hot_path_answers_price_before_ton_gate_without_checkout(self):
        sender = "15555550400"
        result, dry_run, state, _ = self._run_text(
            "Quanto custa a T3?",
            sender=sender,
            initial_qualify={
                "step": "cnpj_ton",
                "pessoa": "cnpj",
                "tipo": "loja_fisica",
                "comprovante": "impresso",
            },
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "hot_preco_modelo_gated")
        self.assertIn("T3", response)
        self.assertIn("R$ 108,00", response)
        self.assertIn("21x", response)
        self.assertNotIn("checkout.example.invalid", response)
        self.assertEqual(
            [button_id for button_id, _ in dry_run.buttons[-1][2]],
            ["cnpj_tem_ton_sim", "cnpj_tem_ton_nao"],
        )
        self.assertLess(
            dry_run.events.index(("text", sender)),
            dry_run.events.index(("buttons", sender)),
        )
        self.assertEqual(state["pessoa"], "cnpj")
        self.assertNotIn("continue_as_cpf", state)

    def test_quanto_e_model_price_answers_before_pending_or_active_cnpj_gate(self):
        cases = (
            (
                "pending",
                {
                    "step": "cnpj_ton",
                    "pessoa": "cnpj",
                    "has_active_ton": False,
                    "modelo": "t3",
                    "tipo": "loja_fisica",
                    "comprovante": "impresso",
                },
                ["cnpj_tem_ton_sim", "cnpj_tem_ton_nao"],
            ),
            (
                "active",
                {
                    "step": "continuar_cpf",
                    "pessoa": "cnpj",
                    "has_active_ton": True,
                    "continue_as_cpf": False,
                    "modelo": "t3",
                    "tipo": "loja_fisica",
                    "comprovante": "impresso",
                },
                ["continuar_cpf", "nao_seguir"],
            ),
        )
        for status, initial_state, expected_buttons in cases:
            with self.subTest(status=status):
                sender = f"155555506{len(status)}00"
                result, dry_run, state, main = self._run_text(
                    "Quanto é a T3?",
                    sender=sender,
                    initial_qualify=initial_state,
                )

                response = " ".join(text for _, text in dry_run.replies)
                self.assertEqual(result["status"], "hot_preco_modelo_gated")
                self.assertIn("R$ 108,00", response)
                self.assertIn("21x", response)
                self.assertNotIn("checkout.example.invalid", response)
                self.assertEqual(
                    [button_id for button_id, _ in dry_run.buttons[-1][2]],
                    expected_buttons,
                )
                self.assertLess(
                    dry_run.events.index(("text", sender)),
                    dry_run.events.index(("buttons", sender)),
                )
                for key in ("pessoa", "has_active_ton", "continue_as_cpf"):
                    self.assertEqual(state.get(key), initial_state.get(key), key)

        self.assertEqual(
            main._detect_intent("Quanto é a taxa?", self._checkout_test_models()),
            "taxa",
        )

    def test_pending_cnpj_generic_price_without_catalog_prices_skips_receipt_question(self):
        sender = "15555550402"
        result, dry_run, _, _ = self._run_text(
            "Quanto custam as maquininhas?",
            sender=sender,
            initial_qualify={
                "step": "cnpj_ton",
                "pessoa": "cnpj",
                "tipo": "loja_fisica",
            },
            empty_model_prices=True,
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "hot_preco_faixa_gated")
        self.assertIn("confirmar os valores atuais", response)
        self.assertNotIn("comprovante", response.lower())
        self.assertNotIn("checkout.example.invalid", response)
        self.assertEqual(
            [button_id for button_id, _ in dry_run.buttons[-1][2]],
            ["cnpj_tem_ton_sim", "cnpj_tem_ton_nao"],
        )
        self.assertLess(
            dry_run.events.index(("text", sender)),
            dry_run.events.index(("buttons", sender)),
        )

    def test_explicit_cpf_continuation_unblocks_text_purchase_hot_path(self):
        sender = "15555550200"
        _, _, state, _ = self._run_text(
            "Quero comprar a T3",
            sender=sender,
            initial_qualify={
                "step": "continuar_cpf",
                "pessoa": "cnpj",
                "has_active_ton": True,
                "continue_as_cpf": False,
                "modelo": "t3",
                "tipo": "loja_fisica",
                "comprovante": "impresso",
            },
        )
        _, _, state, _ = self._run_button(
            "continuar_cpf", sender=sender, initial_qualify=state
        )
        result, dry_run, _, _ = self._run_text(
            "Quero comprar a T3", sender=sender, initial_qualify=state
        )

        self.assertEqual(result["status"], "hot_compra_modelo")
        self.assertIn(
            "https://checkout.example.invalid/mega-plus/t3",
            " ".join(text for _, text in dry_run.replies),
        )

    def test_inactive_cnpj_and_cpf_text_purchase_hot_paths_remain_unrestricted(self):
        for person in ("cnpj", "cpf"):
            with self.subTest(person=person):
                sender = f"155555503{len(person)}"
                result, dry_run, _, _ = self._run_text(
                    "Quero comprar a T3",
                    sender=sender,
                    initial_qualify={
                        "step": "recomendacao",
                        "pessoa": person,
                        "has_active_ton": False,
                        "continue_as_cpf": False,
                        "modelo": "t3",
                        "tipo": "loja_fisica",
                        "comprovante": "impresso",
                    },
                )

                self.assertEqual(result["status"], "hot_compra_modelo")
                self.assertIn(
                    "https://checkout.example.invalid/mega-plus/t3",
                    " ".join(text for _, text in dry_run.replies),
                )

    def test_dispatch_reply_does_not_send_product_photo_for_active_cnpj_doubt(self):
        dry_run, media_calls = self._dispatch_reply_dry_run({
            "step": "duvidas",
            "pessoa": "cnpj",
            "has_active_ton": True,
            "continue_as_cpf": False,
        })

        self.assertIn("Resposta útil para a dúvida.", " ".join(text for _, text in dry_run.replies))
        self.assertEqual(media_calls, [])
        self.assertEqual(dry_run.buttons, [])
        self.assertFalse(
            any("checkout.example.invalid" in text for _, text in dry_run.replies)
        )

    def test_dispatch_reply_never_sends_product_photo_for_eligible_states(self):
        for state in (
            {"step": "duvidas", "pessoa": "cpf", "has_active_ton": True},
            {"step": "duvidas", "pessoa": "cnpj", "has_active_ton": False},
        ):
            with self.subTest(pessoa=state["pessoa"]):
                dry_run, media_calls = self._dispatch_reply_dry_run(state)

                self.assertEqual(media_calls, [])

    def test_baileys_offer_sends_recommendation_text_without_product_media(self):
        result, sent_text, media_calls = self._deliver_offer_dry_run()

        self.assertIn("Oferta textual da T3.", result)
        self.assertTrue(sent_text)
        self.assertEqual(media_calls, [])

    def test_existing_machine_objection_asks_why_without_immediate_t2_offer(self):
        result, dry_run, _, _ = self._run_text(
            "Já tenho maquininha",
            sender="15555550701",
            initial_qualify={},
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "objecao_trocar")
        self.assertIn("o que te faz pensar em trocar", response.lower())
        self.assertNotIn("T2", response)
        self.assertFalse(any("checkout.example.invalid" in text for _, text in dry_run.replies))

    def test_existing_machine_reason_resumes_recommendation_using_stated_pix_need(self):
        sender = "15555550702"
        self._run_text("Já tenho maquininha", sender=sender, initial_qualify={})
        result, dry_run, state, _ = self._run_text(
            "Faço entregas na rua e preciso de Pix",
            sender=sender,
            reset_state=False,
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "objecao_trocar_recomendacao")
        self.assertEqual(state.get("modelo"), "t2")
        self.assertIn("T2", response)
        self.assertIn("Pix", response)
        self.assertNotIn("T1", response)
        self.assertNotIn("porque ela tem comprovante impresso", response)

    def test_link_resend_uses_the_previously_selected_model(self):
        sender = "15555550703"
        self._run_text(
            "Quero comprar a T3",
            sender=sender,
            initial_qualify={"step": "recomendacao", "pessoa": "cpf", "modelo": "t3"},
        )
        result, dry_run, _, _ = self._run_text(
            "Me manda o link",
            sender=sender,
            reset_state=False,
            llm_reply="A T2 também pode servir para você.",
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "hot_link_reenvio")
        self.assertIn("https://checkout.example.invalid/mega-plus/t3", response)
        self.assertNotIn("https://checkout.example.invalid/mega-plus/t2", response)

    def test_sim_and_quero_comprar_send_selected_checkout_without_os_question(self):
        for suffix, reply in (("12", "sim"), ("13", "quero comprar")):
            with self.subTest(reply=reply):
                sender = f"1555555070{suffix}"
                qualify = {"step": "recomendacao", "pessoa": "cpf", "modelo": "t3"}
                self._run_text("Quero comprar a T3", sender=sender, initial_qualify=qualify)
                result, dry_run, _, _ = self._run_text(
                    reply,
                    sender=sender,
                    reset_state=False,
                )

                response = " ".join(text for _, text in dry_run.replies)
                self.assertIn(result["status"], ("link_pos_sim", "hot_compra_modelo"))
                self.assertIn("https://checkout.example.invalid/mega-plus/t3", response)
                self.assertNotRegex(response, r"Android ou iPhone|Seu celular é")

    def test_t1_with_pix_explains_limitation_and_offers_t2(self):
        result, dry_run, _, _ = self._run_text(
            "Quero comprar a T1, mas preciso de Pix",
            sender="15555550705",
            initial_qualify={"step": "recomendacao", "pessoa": "cpf"},
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["modelo"], "t2")
        self.assertIn("A T1 não tem Pix na maquininha", response)
        self.assertIn("T2", response)
        self.assertIn("https://checkout.example.invalid/mega-plus/t2", response)

    def test_t1_with_pix_and_print_need_explains_both_and_offers_printable_model(self):
        result, dry_run, _, _ = self._run_text(
            "Quero comprar a T1; preciso de Pix e comprovante impresso",
            sender="15555550864",
            initial_qualify={"step": "recomendacao", "pessoa": "cpf"},
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["modelo"], "t3")
        self.assertIn("A T1 não tem Pix na maquininha", response)
        self.assertIn("T1 não imprime comprovante", response)
        self.assertIn("T3", response)
        self._assert_checkout_url_contract(
            response, "https://checkout.example.invalid/mega-plus/t3"
        )

    def test_explicit_t2_buy_and_link_routes_redirect_print_need_to_t3(self):
        cases = (
            ("Quero comprar a T2", "hot_compra_modelo"),
            ("Me manda o link da T2; preciso imprimir", "hot_link_reenvio"),
        )
        for index, (message, status) in enumerate(cases):
            with self.subTest(message=message):
                result, dry_run, state, _ = self._run_text(
                    message,
                    sender=f"1555555085{index}",
                    initial_qualify={
                        "step": "recomendacao", "pessoa": "cpf", "modelo": "t2",
                        "comprovante": "impresso",
                    },
                )

                response = " ".join(text for _, text in dry_run.replies)
                self.assertEqual(result["status"], status)
                self.assertEqual(result["modelo"], "t3")
                self.assertEqual(state["modelo"], "t3")
                self.assertIn("T2", response)
                self.assertIn("não imprime comprovante", response.lower())
                self.assertIn("T3", response)
                self._assert_checkout_url_contract(
                    response, "https://checkout.example.invalid/mega-plus/t3"
                )

    def test_previously_requested_t2_is_revalidated_when_print_need_arrives_later(self):
        sender = "15555550857"
        first, _, state, _ = self._run_text(
            "Quero saber da T2",
            sender=sender,
            initial_qualify={"step": "recomendacao", "pessoa": "cpf"},
        )
        self.assertEqual(first["modelo"], "t2")
        self.assertEqual(state["modelo"], "t2")

        result, dry_run, state, _ = self._run_text(
            "Quero comprar; preciso imprimir",
            sender=sender,
            reset_state=False,
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["modelo"], "t3")
        self.assertEqual(state["modelo"], "t3")
        self.assertIn("não imprime comprovante", response.lower())
        self._assert_checkout_url_contract(
            response, "https://checkout.example.invalid/mega-plus/t3"
        )

    def test_latest_explicit_no_print_need_overrides_stale_print_state(self):
        result, dry_run, state, _ = self._run_text(
            "Quero comprar a T2; não preciso de comprovante impresso",
            sender="15555550859",
            initial_qualify={
                "step": "recomendacao", "pessoa": "cpf", "modelo": "t2",
                "comprovante": "impresso",
            },
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["modelo"], "t2")
        self.assertEqual(state["modelo"], "t2")
        self.assertNotIn("não imprime comprovante", response.lower())
        self._assert_checkout_url_contract(
            response, "https://checkout.example.invalid/mega-plus/t2"
        )

    def test_explicit_t2_checkout_with_touchscreen_need_selects_t3_smart(self):
        result, dry_run, state, _ = self._run_text(
            "Quero comprar a T2; preciso de tela Android e 4G",
            sender="15555550860",
            initial_qualify={
                "step": "recomendacao", "pessoa": "cpf", "modelo": "t2",
            },
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["modelo"], "t3_smart")
        self.assertEqual(state["modelo"], "t3_smart")
        self.assertIn("tela Android ou 4G", response)
        self.assertIn("T3 Smart", response)
        self._assert_checkout_url_contract(
            response, "https://checkout.example.invalid/mega-plus/t3-smart"
        )

    def test_latest_print_need_does_not_reuse_stale_digital_receipt_copy(self):
        sender = "15555550861"
        self._run_text(
            "O comprovante digital resolve; quero saber da T2",
            sender=sender,
            initial_qualify={"step": "recomendacao", "pessoa": "cpf"},
        )
        result, dry_run, state, _ = self._run_text(
            "Quero comprar a T2; preciso imprimir",
            sender=sender,
            reset_state=False,
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["modelo"], "t3")
        self.assertEqual(state["modelo"], "t3")
        self.assertIn("comprovante impresso", response.lower())
        self.assertNotIn("comprovante digital já resolve", response.lower())
        self._assert_checkout_url_contract(
            response, "https://checkout.example.invalid/mega-plus/t3"
        )

    def test_android_phone_mention_alone_does_not_create_touchscreen_need(self):
        sender = "15555550862"
        self._run_text(
            "Meu celular é Android",
            sender=sender,
            initial_qualify={"step": "duvidas", "pessoa": "cpf"},
        )
        result, dry_run, state, _ = self._run_text(
            "Quero comprar a T2",
            sender=sender,
            reset_state=False,
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["modelo"], "t2")
        self.assertEqual(state["modelo"], "t2")
        self.assertNotIn("T3 Smart", response)
        self._assert_checkout_url_contract(
            response, "https://checkout.example.invalid/mega-plus/t2"
        )

    def test_existing_machine_recommendation_does_not_treat_android_phone_as_touchscreen_need(self):
        sender = "15555550863"
        self._run_text("Já tenho maquininha", sender=sender, initial_qualify={})
        result, dry_run, state, _ = self._run_text(
            "Meu celular é Android; preciso de comprovante impresso",
            sender=sender,
            reset_state=False,
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "objecao_trocar_recomendacao")
        self.assertEqual(state["modelo"], "t3")
        self.assertIn("comprovante impresso", response.lower())
        self.assertNotIn("T3 Smart", response)
        self._assert_checkout_url_contract(
            response, "https://checkout.example.invalid/mega-plus/t3"
        )

    def test_explicit_t2_price_with_print_need_answers_t2_first_then_links_t3_once(self):
        result, dry_run, state, _ = self._run_text(
            "Quanto custa a T2? Preciso de comprovante impresso.",
            sender="15555550852",
            initial_qualify={
                "step": "recomendacao", "pessoa": "cpf", "modelo": "t2",
                "comprovante": "impresso",
            },
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "hot_preco_modelo")
        self.assertEqual(result["modelo"], "t3")
        self.assertEqual(state["modelo"], "t3")
        self.assertIn("A T2 tem adesão única de R$ 49,88", response)
        self.assertIn("não imprime comprovante", response.lower())
        self.assertIn("a T3", response)
        self.assertLess(response.index("R$ 49,88"), response.lower().index("não imprime comprovante"))
        self.assertLess(response.lower().index("não imprime comprovante"), response.index("https://checkout"))
        self._assert_checkout_url_contract(
            response, "https://checkout.example.invalid/mega-plus/t3"
        )

    def test_partial_t2_catalog_with_print_need_never_sends_checkout_for_text_purchase(self):
        t2_only = [model for model in self._checkout_test_models() if model["id"] == "t2"]
        result, dry_run, state, _ = self._run_text(
            "Quero comprar a T2",
            sender="15555550853",
            initial_qualify={
                "step": "recomendacao", "pessoa": "cpf", "modelo": "t2",
                "comprovante": "impresso",
            },
            models=t2_only,
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "hot_compra_modelo")
        self.assertEqual(state["step"], "atendimento")
        self.assertIn("atendimento humano", response.lower())
        self._assert_checkout_url_contract(response, None)

    def test_partial_t2_catalog_price_answers_requested_model_but_has_no_checkout(self):
        t2_only = [model for model in self._checkout_test_models() if model["id"] == "t2"]
        result, dry_run, state, _ = self._run_text(
            "Quanto custa a T2? Preciso imprimir",
            sender="15555550858",
            initial_qualify={
                "step": "recomendacao", "pessoa": "cpf", "modelo": "t2",
                "comprovante": "impresso",
            },
            models=t2_only,
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "hot_preco_modelo")
        self.assertEqual(state["step"], "atendimento")
        self.assertIn("A T2 tem adesão única de R$ 49,88", response)
        self.assertLess(response.index("R$ 49,88"), response.lower().index("não imprime comprovante"))
        self.assertIn("atendimento humano", response.lower())
        self._assert_checkout_url_contract(response, None)

    def test_buy_button_revalidates_stale_incompatible_selected_model(self):
        catalogs = (
            (self._checkout_test_models(), "https://checkout.example.invalid/mega-plus/t3"),
            ([model for model in self._checkout_test_models() if model["id"] == "t2"], None),
        )
        for index, (models, expected_link) in enumerate(catalogs):
            with self.subTest(expected_link=expected_link):
                result, dry_run, state, _ = self._run_button(
                    "comprar",
                    sender=f"1555555085{4 + index}",
                    initial_qualify={
                        "step": "recomendacao", "pessoa": "cpf", "modelo": "t2",
                        "comprovante": "impresso",
                    },
                    models=models,
                )

                response = " ".join(text for _, text in dry_run.replies)
                if expected_link:
                    self.assertEqual(result["status"], "btn_comprar")
                    self.assertEqual(state["modelo"], "t3")
                    self.assertIn("não imprime comprovante", response.lower())
                    self.assertIn("T3", response)
                else:
                    self.assertEqual(result["status"], "btn_sem_modelo")
                    self.assertEqual(state["step"], "atendimento")
                    self.assertIn("atendimento humano", response.lower())
                self._assert_checkout_url_contract(response, expected_link)

    def test_affirmative_revalidates_stale_incompatible_selected_model(self):
        sender = "15555550855"
        _, _, _, main = self._run_text(
            "Quero comprar a T2",
            sender=sender,
            initial_qualify={
                "step": "recomendacao", "pessoa": "cpf", "modelo": "t2",
                "comprovante": "impresso",
            },
        )
        main.QUALIFY[sender]["modelo"] = "t2"

        result, dry_run, state, _ = self._run_text(
            "sim", sender=sender, reset_state=False
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "link_pos_sim")
        self.assertEqual(state["modelo"], "t3")
        self.assertIn("não imprime comprovante", response.lower())
        self._assert_checkout_url_contract(
            response, "https://checkout.example.invalid/mega-plus/t3"
        )

    def test_explicitly_not_needing_pix_keeps_the_requested_t1(self):
        result, dry_run, _, _ = self._run_text(
            "Quero comprar a T1, não preciso de Pix",
            sender="15555550716",
            initial_qualify={"step": "recomendacao", "pessoa": "cpf"},
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["modelo"], "t1")
        self.assertIn("https://checkout.example.invalid/mega-plus/t1", response)

    def test_t1_price_question_with_pix_need_offers_t2_not_t1(self):
        result, dry_run, _, _ = self._run_text(
            "Quanto custa a T1? Preciso de Pix",
            sender="15555550717",
            initial_qualify={"step": "recomendacao", "pessoa": "cpf"},
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["modelo"], "t2")
        self.assertIn("A T1 não tem Pix na maquininha", response)
        self.assertIn("https://checkout.example.invalid/mega-plus/t2", response)
        self.assertNotIn("https://checkout.example.invalid/mega-plus/t1", response)

    def test_t1_price_with_pix_and_no_t2_only_explains_limitation(self):
        only_t1 = [m for m in self._checkout_test_models() if m["id"] == "t1"]
        result, dry_run, _, _ = self._run_text(
            "Quanto custa a T1? Preciso de Pix",
            sender="15555550718",
            initial_qualify={"step": "recomendacao", "pessoa": "cpf"},
            models=only_t1,
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "hot_t1_pix_indisponivel")
        self.assertIn("A T1 não tem Pix na maquininha", response)
        self.assertNotIn("checkout.example.invalid", response)

    def test_later_no_pix_preference_overrides_earlier_pix_need_for_t1(self):
        sender = "15555550722"
        self._run_text(
            "Preciso de Pix",
            sender=sender,
            initial_qualify={"step": "recomendacao", "pessoa": "cpf"},
        )
        result, dry_run, _, _ = self._run_text(
            "Quero a T1, não preciso mais de Pix",
            sender=sender,
            reset_state=False,
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["modelo"], "t1")
        self.assertIn("https://checkout.example.invalid/mega-plus/t1", response)
        self.assertNotIn("https://checkout.example.invalid/mega-plus/t2", response)

    def test_later_no_vou_precisar_pix_preference_keeps_t1_and_its_link(self):
        sender = "15555550725"
        self._run_text(
            "Preciso de Pix",
            sender=sender,
            initial_qualify={"step": "recomendacao", "pessoa": "cpf"},
        )
        result, dry_run, _, _ = self._run_text(
            "Quero a T1, não vou precisar de Pix",
            sender=sender,
            reset_state=False,
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["modelo"], "t1")
        self.assertIn("https://checkout.example.invalid/mega-plus/t1", response)
        self.assertNotIn("https://checkout.example.invalid/mega-plus/t2", response)

    def test_single_message_no_vou_precisar_pix_keeps_t1_and_its_link(self):
        result, dry_run, _, _ = self._run_text(
            "Quero a T1, não vou precisar de Pix",
            sender="15555550726",
            initial_qualify={"step": "recomendacao", "pessoa": "cpf"},
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["modelo"], "t1")
        self.assertIn("https://checkout.example.invalid/mega-plus/t1", response)
        self.assertNotIn("https://checkout.example.invalid/mega-plus/t2", response)

    def test_later_pix_need_overrides_earlier_no_pix_preference(self):
        sender = "15555550723"
        self._run_text(
            "Não preciso de Pix",
            sender=sender,
            initial_qualify={"step": "recomendacao", "pessoa": "cpf"},
        )
        result, dry_run, _, _ = self._run_text(
            "Quero a T1, agora preciso de Pix",
            sender=sender,
            reset_state=False,
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["modelo"], "t2")
        self.assertIn("A T1 não tem Pix na maquininha", response)
        self.assertIn("https://checkout.example.invalid/mega-plus/t2", response)
        self.assertNotIn("https://checkout.example.invalid/mega-plus/t1", response)

    def test_last_explicit_pix_preference_wins_inside_one_message(self):
        cases = (
            (
                "Preciso de Pix, mas agora não preciso mais de Pix. Quero a T1",
                "t1",
                "https://checkout.example.invalid/mega-plus/t1",
            ),
            (
                "Não preciso de Pix, mudei de ideia e agora preciso de Pix. Quero a T1",
                "t2",
                "https://checkout.example.invalid/mega-plus/t2",
            ),
        )
        for index, (text, expected_model, expected_link) in enumerate(cases):
            with self.subTest(text=text):
                result, dry_run, _, _ = self._run_text(
                    text,
                    sender=f"1555555072{4 + index}",
                    initial_qualify={"step": "recomendacao", "pessoa": "cpf"},
                )

                response = " ".join(message for _, message in dry_run.replies)
                self.assertEqual(result["modelo"], expected_model)
                self.assertIn(expected_link, response)
                unexpected_model = "t2" if expected_model == "t1" else "t1"
                self.assertNotIn(f"https://checkout.example.invalid/mega-plus/{unexpected_model}", response)

    def test_discount_reply_uses_selected_model_existing_black_link(self):
        result, dry_run, _, _ = self._run_text(
            "Tem desconto na T3?",
            sender="15555550719",
            initial_qualify={
                "step": "recomendacao", "pessoa": "cnpj", "has_active_ton": False,
                "continue_as_cpf": False, "volume": "30k_plus",
            },
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "hot_desconto")
        self.assertIn("Pode existir desconto na adesão pelo nosso link, conforme a condição disponível no momento. Não é um valor garantido e não há custo extra para você.", response)
        self.assertIn("https://checkout.example.invalid/black/t3", response)
        self.assertNotIn("https://checkout.example.invalid/mega-plus/t3", response)

    def _assert_promo_snapshot_response(self, response, model_id, affiliate_link):
        expected = MODEL_PROMO_EXPECTED[model_id]
        self.assertIn(expected["desconto"], response)
        self.assertIn(expected["preco_observado"], response)
        self.assertIn(expected["observado_em"], response)
        self.assertRegex(response.lower(), r"promo.{0,80}podem mudar|podem mudar.{0,80}promo")
        self.assertRegex(response.lower(), r"carrinho.*(?:confirme|final|link)|(?:confirme|final|link).*carrinho")
        self.assertIn(affiliate_link, response)
        self.assertEqual(response.count(affiliate_link), 1)
        self.assertEqual(response.count("https://checkout.example.invalid/"), 1)
        self.assertNotIn("cashback", response.lower())
        if expected["cupom"] is None:
            self.assertIn("sem cupom extra", response.lower())
            self.assertNotIn("cupom 20%", response.lower())
        else:
            self.assertIn("cupom 20%", response.lower())

    def test_buy_button_uses_each_models_promo_snapshot_and_affiliate_link(self):
        for index, (model_id, expected) in enumerate(MODEL_PROMO_EXPECTED.items()):
            with self.subTest(model=model_id):
                link = f"https://checkout.example.invalid/mega-plus/{expected['link_slug']}"
                result, dry_run, _, _ = self._run_button(
                    "comprar",
                    sender=f"1555555073{index}",
                    initial_qualify={"step": "recomendacao", "pessoa": "cpf", "modelo": model_id},
                )

                response = " ".join(text for _, text in dry_run.replies)
                self.assertEqual(result["status"], "btn_comprar")
                self._assert_promo_snapshot_response(response, model_id, link)

    def test_button_recommendation_includes_selected_model_snapshot_before_checkout_button(self):
        result, dry_run, _, _ = self._run_button(
            "cpf",
            sender="15555550752",
            initial_qualify={"step": "pessoa", "comprovante": "digital"},
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "btn_recomendacao")
        self.assertEqual(result["modelo"], "t2")
        self.assertIn("72% OFF", response)
        self.assertIn("cupom 20%", response)
        self.assertIn("R$ 39,90", response)
        self.assertIn("07/10/2026", response)
        self.assertIn("Promoção e preço podem mudar", response)
        self.assertIn("confirme disponibilidade e valor final no carrinho", response)
        self.assertNotIn("checkout.example.invalid", response)

    def test_deliver_offer_price_and_text_purchase_include_each_model_snapshot(self):
        for index, (model_id, expected) in enumerate(MODEL_PROMO_EXPECTED.items()):
            link = f"https://checkout.example.invalid/mega-plus/{expected['link_slug']}"
            for route, text, status in (
                ("price", f"Quanto custa a {expected['nome']}?", "hot_preco_modelo"),
                ("buy", f"Quero comprar a {expected['nome']}", "hot_compra_modelo"),
            ):
                with self.subTest(model=model_id, route=route):
                    result, dry_run, _, _ = self._run_text(
                        text,
                        sender=f"1555555075{index}{route[0]}",
                        initial_qualify={"step": "recomendacao", "pessoa": "cpf"},
                    )

                    response = " ".join(message for _, message in dry_run.replies)
                    self.assertEqual(result["status"], status)
                    self.assertEqual(result["modelo"], model_id)
                    self._assert_promo_snapshot_response(response, model_id, link)

    def test_affirmative_and_selected_model_link_resend_include_snapshot(self):
        for suffix, followup in (("1", "sim"), ("2", "quero comprar"), ("3", "Me manda o link")):
            with self.subTest(followup=followup):
                sender = f"1555555076{suffix}"
                self._run_text(
                    "Quero comprar a T1",
                    sender=sender,
                    initial_qualify={"step": "recomendacao", "pessoa": "cpf"},
                )
                result, dry_run, _, _ = self._run_text(
                    followup,
                    sender=sender,
                    reset_state=False,
                )

                response = " ".join(message for _, message in dry_run.replies)
                self.assertIn(result["status"], ("link_pos_sim", "hot_compra_modelo", "hot_link_reenvio"))
                self._assert_promo_snapshot_response(
                    response, "t1", "https://checkout.example.invalid/mega-plus/t1"
                )

    def test_checkout_without_model_snapshot_uses_cautious_fallback_once(self):
        models = [model for model in self._checkout_test_models() if model["id"] == "t1"]
        models[0].pop("promocao")
        result, dry_run, _, _ = self._run_text(
            "Quero comprar a T1",
            sender="15555550764",
            initial_qualify={"step": "recomendacao", "pessoa": "cpf"},
            models=models,
            base_conhecimento="",
        )

        response = " ".join(message for _, message in dry_run.replies)
        self.assertEqual(result["status"], "hot_compra_modelo")
        self.assertIn("Não tenho um snapshot promocional confirmado para T1", response)
        self.assertIn("preço final podem mudar", response)
        self.assertIn("confira no carrinho", response)
        self.assertEqual(response.count("https://checkout.example.invalid/mega-plus/t1"), 1)
        self.assertEqual(response.count("https://checkout.example.invalid/"), 1)
        self.assertNotRegex(response, r"\b(?:70|71|72)% OFF|cupom")

    def test_discount_and_price_objection_routes_use_each_model_snapshot(self):
        for index, (model_id, expected) in enumerate(MODEL_PROMO_EXPECTED.items()):
            link = f"https://checkout.example.invalid/mega-plus/{expected['link_slug']}"
            for route, text, status in (
                ("discount", f"Tem desconto na {expected['nome']}?", "hot_desconto"),
                ("objection", "Tá caro.", "hot_preco_objecao"),
            ):
                with self.subTest(model=model_id, route=route):
                    result, dry_run, _, _ = self._run_text(
                        text,
                        sender=f"1555555074{index}{route[0]}",
                        initial_qualify={"step": "recomendacao", "pessoa": "cpf", "modelo": model_id},
                    )
                    response = " ".join(message for _, message in dry_run.replies)
                    self.assertEqual(result["status"], status)
                    self._assert_promo_snapshot_response(response, model_id, link)

    def test_selected_model_promo_snapshot_can_be_read_from_base_when_model_field_absent(self):
        config_example = json.loads(
            (Path(__file__).resolve().parents[1] / "config" / "agent-config.example.json").read_text(encoding="utf-8")
        )
        models = self._checkout_test_models()
        for model in models:
            model.pop("promocao", None)
        result, dry_run, _, _ = self._run_text(
            "Tem desconto na T2?",
            sender="15555550748",
            initial_qualify={"step": "recomendacao", "pessoa": "cpf", "modelo": "t2"},
            models=models,
            base_conhecimento=config_example["base_conhecimento"],
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "hot_desconto")
        self._assert_promo_snapshot_response(
            response,
            "t2",
            "https://checkout.example.invalid/mega-plus/t2",
        )

    def test_llm_model_recommendation_uses_snapshot_and_only_selected_black_checkout(self):
        unconfigured_link = "https://attacker.example.invalid/track"
        result, dry_run, _, _ = self._run_text(
            "Pode me explicar melhor?",
            sender="15555550780",
            initial_qualify={
                "step": "recomendacao", "pessoa": "cnpj", "has_active_ton": False,
                "continue_as_cpf": False, "volume": "30k_plus",
            },
            llm_reply=f"A T3 combina com o seu uso. Veja aqui: {unconfigured_link}",
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "sent")
        self.assertIn("A T3 combina com o seu uso.", response)
        self.assertIn("Não tenho um snapshot promocional confirmado para T3", response)
        self.assertIn("confira no carrinho", response)
        self.assertIn("https://checkout.example.invalid/black/t3", response)
        self.assertEqual(response.count("https://checkout.example.invalid/"), 1)
        self.assertNotIn("72% OFF", response)
        self.assertNotIn("cupom 20%", response.lower())
        self.assertNotIn("07/10/2026", response)
        self.assertNotIn("Veja aqui:", response)
        self.assertIn("A T3 combina com o seu uso.", response)
        self.assertNotIn(unconfigured_link, response)

    def test_llm_model_recommendation_replaces_conflicting_commercial_claims(self):
        result, dry_run, _, _ = self._run_text(
            "Pode me explicar melhor?",
            sender="15555550786",
            initial_qualify={
                "step": "recomendacao", "pessoa": "cpf", "modelo": "t3",
            },
            llm_reply=(
                "A T3 combina com o comprovante impresso que você precisa; "
                "custa R$ 1,00, tem 95% OFF e cupom de 80%; "
                "também é uma opção prática para o balcão."
            ),
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "sent")
        self.assertIn("A T3 combina com o comprovante impresso que você precisa", response)
        self.assertIn("também é uma opção prática para o balcão.", response)
        self.assertNotIn("R$ 1,00", response)
        self.assertNotIn("95% OFF", response)
        self.assertNotIn("cupom de 80%", response.lower())
        self._assert_promo_snapshot_response(
            response, "t3", "https://checkout.example.invalid/mega-plus/t3"
        )

    def test_llm_checkout_removes_written_currency_and_installment_claims(self):
        result, dry_run, _, _ = self._run_text(
            "Pode me explicar melhor?",
            sender="15555550801",
            initial_qualify={
                "step": "recomendacao", "pessoa": "cpf", "modelo": "t3",
            },
            llm_reply=(
                "A T3 é prática para o balcão. Ela sai por 80 reais. "
                "Pode ser parcelada em 12 vezes."
            ),
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "sent")
        self.assertIn("A T3 é prática para o balcão.", response)
        self.assertNotIn("80 reais", response.lower())
        self.assertNotIn("12 vezes", response.lower())
        self._assert_promo_snapshot_response(
            response, "t3", "https://checkout.example.invalid/mega-plus/t3"
        )

    def test_llm_checkout_removes_written_out_money_amounts_but_keeps_verified_copy(self):
        result, dry_run, _, _ = self._run_text(
            "Pode me explicar melhor?",
            sender="15555550802",
            initial_qualify={
                "step": "recomendacao", "pessoa": "cpf", "modelo": "t3",
            },
            llm_reply=(
                "A T3 é prática para o balcão. O investimento fica em oitenta reais. "
                "A adesão sai por cento e vinte reais."
            ),
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "sent")
        self.assertIn("A T3 é prática para o balcão.", response)
        self.assertNotIn("oitenta reais", response.lower())
        self.assertNotIn("cento e vinte reais", response.lower())
        self._assert_promo_snapshot_response(
            response, "t3", "https://checkout.example.invalid/mega-plus/t3"
        )

    def test_active_catalog_price_copy_uses_approved_date(self):
        from admin.tests.whatsapp_test_harness import _load_main

        main = _load_main()
        model = self._checkout_test_models()[2]
        recommendation = main._rec_package(model, {})
        price_anchor = main._price_anchor("Tá caro.", model)

        for output in (recommendation, price_anchor):
            with self.subTest(output=output.splitlines()[0]):
                self.assertIn("07/10/2026", output)
                self.assertNotIn("06/10/2026", output)

    def test_llm_free_audio_claims_and_urls_are_sanitized(self):
        unconfigured_link = "https://attacker.example.invalid/voice"
        result, dry_run, _, _ = self._run_text(
            "Pode me explicar melhor?",
            sender="15555550805",
            initial_qualify={
                "step": "recomendacao", "pessoa": "cpf", "modelo": "t3",
            },
            llm_reply=(
                "A T3 é prática para o balcão. "
                f'[ÁUDIO: "A T3 sai por 80 reais. Acesse {unconfigured_link}"]'
            ),
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "sent")
        self.assertIn("A T3 é prática para o balcão.", response)
        self.assertNotIn("80 reais", response.lower())
        self.assertNotIn(unconfigured_link, response)
        self._assert_promo_snapshot_response(
            response, "t3", "https://checkout.example.invalid/mega-plus/t3"
        )

    def test_llm_doubt_without_selected_model_strips_configured_checkout_but_keeps_app_and_help(self):
        checkout = "https://checkout.example.invalid/redirect/cart"
        app = "https://example.invalid/app"
        help_url = "https://help.example.invalid/faq"
        sender = "15555550806"
        models = [self._checkout_test_models()[2] | {"link": checkout}]
        result, dry_run, _, _ = self._run_text(
            "Pode esclarecer isso?",
            sender=sender,
            llm_reply=(
                f"Acesse o app: {app}. Ajuda oficial: {help_url}. "
                f"Checkout: {checkout}"
            ),
            base_conhecimento=f"Ajuda oficial: {help_url}",
            models=models,
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "sent")
        self.assertIn(app, response)
        self.assertIn(help_url, response)
        self.assertNotIn(checkout, response)
        self.assertNotIn("checkout", response.lower())

    def test_llm_selected_checkout_appends_exactly_the_selected_configured_link(self):
        selected_link = "https://checkout.example.invalid/mega-plus/t2"
        sender = "15555550807"
        result, dry_run, _, _ = self._run_text(
            "Pode me explicar melhor?",
            sender=sender,
            initial_qualify={
                sender: {"step": "recomendacao", "pessoa": "cpf", "modelo": "t2"},
            },
            llm_reply="A T2 combina com o seu uso.",
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "sent")
        self.assertEqual(self._checkout_urls(response), [selected_link])

    def test_llm_checkout_attachment_recommends_only_model_compatible_with_current_need(self):
        result, dry_run, state, _ = self._run_text(
            "Pode me explicar melhor?",
            sender="15555550856",
            initial_qualify={
                "step": "recomendacao", "pessoa": "cpf", "modelo": "t2",
                "comprovante": "impresso",
            },
            llm_reply="A T2 funciona para esse comprovante e pode ser comprada aqui.",
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["modelo"], "t3")
        self.assertEqual(state["modelo"], "t3")
        self.assertNotIn("T2 funciona", response)
        self.assertIn("T2", response)
        self.assertIn("não imprime comprovante", response.lower())
        self.assertIn("T3", response)
        self._assert_checkout_url_contract(
            response, "https://checkout.example.invalid/mega-plus/t3"
        )

    def test_support_audio_marker_pitch_is_filtered_but_safe_support_text_remains(self):
        result, dry_run, _, _ = self._run_text(
            "Minha maquininha parou de funcionar e preciso de suporte.",
            sender="15555550811",
            initial_qualify={"step": "posvenda"},
            llm_reply=(
                'Estou verificando o ocorrido. '
                '[ÁUDIO: "Sugiro comprar uma T2 agora."]'
            ),
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "sent")
        self.assertIn("Estou verificando o ocorrido.", response)
        self.assertNotRegex(response.lower(), r"sugiro comprar|t2 agora|checkout\.example\.invalid")
        self.assertNotRegex(response.lower(), r"\blink\b")

    def test_support_audio_offer_cta_is_filtered_and_neutral_fallback_remains(self):
        result, dry_run, _, _ = self._run_text(
            "Minha máquina não liga.",
            sender="15555550814",
            initial_qualify={"step": "posvenda"},
            llm_reply='[ÁUDIO: "Aproveite a oferta e compre uma T2 agora."]',
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "sent")
        self.assertIn("Entendi. Vou encaminhar seu caso para atendimento humano.", response)
        self.assertNotRegex(response.lower(), r"aproveite|oferta|compre|t2 agora|checkout|\blink\b")

    def test_support_text_filters_offer_buy_checkout_and_guarantee_ctas(self):
        result, dry_run, _, _ = self._run_text(
            "Minha máquina não liga.",
            sender="15555550815",
            initial_qualify={"step": "posvenda"},
            llm_reply=(
                "Aproveite a oferta e compre uma T2 agora. "
                "Garanta sua T3 pelo checkout. "
                "Vamos verificar seu pagamento."
            ),
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "sent")
        self.assertIn("Vamos verificar seu pagamento.", response)
        self.assertNotRegex(response.lower(), r"aproveite|oferta|compre|garanta|checkout|t2|t3")

    def test_support_contrast_clause_pitch_is_removed_from_text_and_audio(self):
        mixed = "Não consegui resolver, mas aproveite a oferta e compre uma T2 agora."
        for mode in ("text", "audio"):
            with self.subTest(mode=mode):
                llm_reply = mixed if mode == "text" else f'[ÁUDIO: "{mixed}"]'
                result, dry_run, _, main = self._run_text(
                    "Minha máquina não liga.",
                    sender=f"1555555084{0 if mode == 'text' else 1}",
                    initial_qualify={"step": "posvenda"},
                    llm_reply=llm_reply,
                )

                response = " ".join(text for _, text in dry_run.replies)
                transcript = " ".join(
                    message["content"]
                    for message in main._history_get(f"1555555084{0 if mode == 'text' else 1}")
                    if message.get("role") == "assistant"
                )
                output = f"{response} {transcript}"
                self.assertEqual(result["status"], "sent")
                self.assertIn("Não consegui resolver", output)
                self.assertNotRegex(output.lower(), r"aproveite|oferta|compre|t2 agora")
                self.assertEqual(main._strip_support_sales_pitch(mixed), "Não consegui resolver.")

    def test_support_semicolon_clause_pitch_is_removed_from_text_and_audio(self):
        mixed = "Não consegui resolver; sugiro comprar uma T2 agora."
        for mode in ("text", "audio"):
            with self.subTest(mode=mode):
                llm_reply = mixed if mode == "text" else f'[ÁUDIO: "{mixed}"]'
                sender = f"1555555084{3 if mode == 'text' else 4}"
                result, dry_run, _, main = self._run_text(
                    "Minha máquina não liga.",
                    sender=sender,
                    initial_qualify={"step": "posvenda"},
                    llm_reply=llm_reply,
                )

                response = " ".join(text for _, text in dry_run.replies)
                transcript = " ".join(
                    message["content"]
                    for message in main._history_get(sender)
                    if message.get("role") == "assistant"
                )
                output = f"{response} {transcript}"
                self.assertEqual(result["status"], "sent")
                self.assertIn("Não consegui resolver", output)
                self.assertNotRegex(output.lower(), r"sugiro comprar|t2 agora|checkout")
                self.assertEqual(main._strip_support_sales_pitch(mixed), "Não consegui resolver.")

    def test_support_comma_clause_pitch_is_removed_from_text_and_audio(self):
        mixed = "Não consegui resolver, sugiro comprar uma T2 agora."
        for mode in ("text", "audio"):
            with self.subTest(mode=mode):
                llm_reply = mixed if mode == "text" else f'[ÁUDIO: "{mixed}"]'
                sender = f"1555555084{5 if mode == 'text' else 6}"
                result, dry_run, _, main = self._run_text(
                    "Minha máquina não liga.",
                    sender=sender,
                    initial_qualify={"step": "posvenda"},
                    llm_reply=llm_reply,
                )

                response = " ".join(text for _, text in dry_run.replies)
                transcript = " ".join(
                    message["content"]
                    for message in main._history_get(sender)
                    if message.get("role") == "assistant"
                )
                output = f"{response} {transcript}"
                self.assertEqual(result["status"], "sent")
                self.assertIn("Não consegui resolver", output)
                self.assertNotRegex(output.lower(), r"sugiro comprar|t2 agora|checkout")
                self.assertEqual(main._strip_support_sales_pitch(mixed), "Não consegui resolver.")

    def test_support_pitch_after_e_is_removed_from_text_and_audio(self):
        mixed = "Não consegui resolver e sugiro comprar uma T2 agora."
        pure_refusal = "Não recomendo comprar uma T2 agora."
        for mode in ("text", "audio"):
            with self.subTest(mode=mode):
                sender = f"1555555084{7 if mode == 'text' else 8}"
                llm_reply = mixed if mode == "text" else f'[ÁUDIO: "{mixed}"]'
                result, dry_run, _, main = self._run_text(
                    "Minha máquina não liga.",
                    sender=sender,
                    initial_qualify={"step": "posvenda"},
                    llm_reply=llm_reply,
                )

                response = " ".join(text for _, text in dry_run.replies)
                transcript = " ".join(
                    message["content"]
                    for message in main._history_get(sender)
                    if message.get("role") == "assistant"
                )
                output = f"{response} {transcript}"
                self.assertEqual(result["status"], "sent")
                self.assertIn("Não consegui resolver", output)
                self.assertNotRegex(output.lower(), r"sugiro comprar|t2 agora|checkout")
                self.assertEqual(main._strip_support_sales_pitch(mixed), "Não consegui resolver.")
                self.assertEqual(main._strip_support_sales_pitch(pure_refusal), pure_refusal)

    def test_support_preserves_handoff_before_pitch_after_multiple_e_in_text_and_audio(self):
        mixed = "Não resolvi e vou chamar o suporte e sugiro comprar uma T2 agora."
        expected = "Não resolvi. Vou chamar o suporte."
        for mode in ("text", "audio"):
            with self.subTest(mode=mode):
                sender = f"15555550849{0 if mode == 'text' else 1}"
                llm_reply = mixed if mode == "text" else f'[ÁUDIO: "{mixed}"]'
                result, dry_run, _, main = self._run_text(
                    "Minha máquina não liga.",
                    sender=sender,
                    initial_qualify={"step": "posvenda"},
                    llm_reply=llm_reply,
                )

                response = " ".join(text for _, text in dry_run.replies)
                transcript = " ".join(
                    message["content"]
                    for message in main._history_get(sender)
                    if message.get("role") == "assistant"
                )
                self.assertEqual(result["status"], "sent")
                self.assertEqual(main._strip_support_sales_pitch(mixed), expected)
                self.assertIn(expected, f"{response} {transcript}")

    def test_support_preserves_handoff_before_pitch_without_negation_in_text_and_audio(self):
        mixed = "Vou chamar o suporte e sugiro comprar uma T2 agora."
        expected = "Vou chamar o suporte."
        for mode in ("text", "audio"):
            with self.subTest(mode=mode):
                sender = f"15555550850{0 if mode == 'text' else 1}"
                llm_reply = mixed if mode == "text" else f'[ÁUDIO: "{mixed}"]'
                result, dry_run, _, main = self._run_text(
                    "Minha máquina não liga.",
                    sender=sender,
                    initial_qualify={"step": "posvenda"},
                    llm_reply=llm_reply,
                )

                response = " ".join(text for _, text in dry_run.replies)
                transcript = " ".join(
                    message["content"]
                    for message in main._history_get(sender)
                    if message.get("role") == "assistant"
                )
                output = f"{response} {transcript}"
                self.assertEqual(result["status"], "sent")
                self.assertEqual(main._strip_support_sales_pitch(mixed), expected)
                self.assertIn(expected, output)
                self.assertNotRegex(output.lower(), r"sugiro comprar|t2 agora|checkout")

    def test_support_checkout_navigation_pitch_is_removed_but_refusal_remains_text_and_audio(self):
        pitch = "Aproveite a oferta e vai ao checkout."
        refusals = ("Não vai ao checkout.", "Não vá ao checkout.")
        for mode in ("text", "audio"):
            with self.subTest(mode=mode):
                sender = f"15555550852{0 if mode == 'text' else 1}"
                pitch_reply = pitch if mode == "text" else f'[ÁUDIO: "{pitch}"]'
                result, dry_run, _, main = self._run_text(
                    "Minha máquina não liga.",
                    sender=sender,
                    initial_qualify={"step": "posvenda"},
                    llm_reply=pitch_reply,
                )

                response = " ".join(text for _, text in dry_run.replies)
                transcript = " ".join(
                    message["content"]
                    for message in main._history_get(sender)
                    if message.get("role") == "assistant"
                )
                output = f"{response} {transcript}"
                self.assertEqual(result["status"], "sent")
                self.assertIn("Entendi. Vou encaminhar seu caso para atendimento humano.", output)
                self.assertNotRegex(output.lower(), r"aproveite|oferta|checkout")
                self.assertEqual(main._strip_support_sales_pitch(pitch), "")
                for navigation in (
                    "vá ao checkout", "va ao checkout",
                    "vai ao checkout", "VAI PARA O CHECKOUT",
                    "VÁ PARA O CHECKOUT", "VA PARA O CHECKOUT",
                ):
                    self.assertEqual(
                        main._strip_support_sales_pitch(f"Aproveite a oferta e {navigation}."),
                        "",
                    )

                for refusal_index, refusal in enumerate(refusals):
                    refusal_reply = refusal if mode == "text" else f'[ÁUDIO: "{refusal}"]'
                    refusal_sender = f"1555555085{3 + refusal_index}{0 if mode == 'text' else 1}"
                    refusal_result, refusal_run, _, refusal_main = self._run_text(
                        "Minha máquina não liga.",
                        sender=refusal_sender,
                        initial_qualify={"step": "posvenda"},
                        llm_reply=refusal_reply,
                    )
                    refusal_copy = " ".join(text for _, text in refusal_run.replies)
                    refusal_transcript = " ".join(
                        message["content"]
                        for message in refusal_main._history_get(refusal_sender)
                        if message.get("role") == "assistant"
                    )
                    self.assertEqual(refusal_result["status"], "sent")
                    self.assertIn(refusal, f"{refusal_copy} {refusal_transcript}")
                    self.assertEqual(refusal_main._strip_support_sales_pitch(refusal), refusal)

    def test_support_unrelated_negation_does_not_keep_later_checkout_pitch_in_text_and_audio(self):
        mixed = "Não vai perder tempo: vai ao checkout."
        for mode in ("text", "audio"):
            with self.subTest(mode=mode):
                sender = f"1555555090{0 if mode == 'text' else 1}"
                llm_reply = mixed if mode == "text" else f'[ÁUDIO: "{mixed}"]'
                result, dry_run, _, main = self._run_text(
                    "Minha máquina não liga.",
                    sender=sender,
                    initial_qualify={"step": "posvenda"},
                    llm_reply=llm_reply,
                )

                response = " ".join(text for _, text in dry_run.replies)
                transcript = " ".join(
                    message["content"]
                    for message in main._history_get(sender)
                    if message.get("role") == "assistant"
                )
                output = f"{response} {transcript}"
                self.assertEqual(result["status"], "sent")
                self.assertIn("Entendi. Vou encaminhar seu caso para atendimento humano.", output)
                self.assertNotRegex(output.lower(), r"checkout|perder tempo.*vai ao checkout")
                self.assertNotRegex(main._strip_support_sales_pitch(mixed).lower(), r"checkout|vai ao checkout")

    def test_checkout_navigation_pitch_is_unchanged_on_normal_sales_path(self):
        pitch = "Aproveite a oferta e vá ao checkout."
        result, dry_run, _, _ = self._run_text(
            "Pode me explicar melhor?",
            sender="15555550854",
            initial_qualify={"step": "recomendacao", "pessoa": "cpf", "modelo": "t2"},
            llm_reply=pitch,
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "sent")
        self.assertIn(pitch, response)

    def test_contrast_clause_phrase_is_unchanged_on_normal_sales_path(self):
        mixed = "Não consegui resolver, mas aproveite a oferta e compre uma T2 agora."
        result, dry_run, _, _ = self._run_text(
            "Pode me explicar melhor?",
            sender="15555550842",
            initial_qualify={"step": "recomendacao", "pessoa": "cpf", "modelo": "t2"},
            llm_reply=mixed,
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "sent")
        self.assertIn(mixed, response)

    def test_support_audio_filter_preserves_explicit_purchase_negations(self):
        result, dry_run, _, _ = self._run_text(
            "Minha máquina não liga.",
            sender="15555550816",
            initial_qualify={"step": "posvenda"},
            llm_reply=(
                '[ÁUDIO: "Não compre uma T2. '
                'Não recomendo comprar outra máquina. '
                'Sugiro não comprar agora."]'
            ),
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "sent")
        self.assertIn("Não compre uma T2.", response)
        self.assertIn("Não recomendo comprar outra máquina.", response)
        self.assertIn("Sugiro não comprar agora.", response)

    def test_positive_audio_cta_is_unchanged_on_commercial_path(self):
        cta = "Aproveite a oferta e compre uma T2 agora."
        result, dry_run, _, _ = self._run_text(
            "Pode me explicar melhor?",
            sender="15555550817",
            initial_qualify={"step": "recomendacao", "pessoa": "cpf", "modelo": "t2"},
            llm_reply=f'[ÁUDIO: "{cta}"]',
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "sent")
        self.assertIn(cta, response)

    def test_support_fixed_closing_audio_key_is_suppressed(self):
        result, dry_run, _, _ = self._run_text(
            "Meu pagamento não caiu, preciso de ajuda.",
            sender="15555550812",
            initial_qualify={"step": "posvenda"},
            llm_reply="Estou verificando seu pagamento. [ÁUDIO: fechamento]",
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "sent")
        self.assertIn("Estou verificando seu pagamento.", response)
        self.assertNotRegex(
            response.lower(),
            r"condi[çc][ãa]o de novos clientes|aproveita pagando|link na hora|cpf.{0,40}cnpj",
        )
        self.assertNotRegex(response.lower(), r"\blink\b|\baudio\b|\b[aá]udio\b")
        self.assertNotIn("checkout.example.invalid", response)

    def test_fixed_closing_audio_marker_is_suppressed_in_doubt_or_without_model(self):
        cases = (
            ("doubt", {"step": "duvidas", "pessoa": "cpf", "modelo": "t3"}),
            ("no_model", {}),
        )
        for index, (name, state) in enumerate(cases):
            with self.subTest(mode=name):
                result, dry_run, _, _ = self._run_text(
                    "Pode me explicar isso?",
                    sender=f"1555555081{index + 4}",
                    initial_qualify=state,
                    llm_reply="[ÁUDIO: fechamento]",
                )

                response = " ".join(text for _, text in dry_run.replies)
                self.assertEqual(result["status"], "sent")
                self.assertIn("Pode mandar sua dúvida por aqui.", response)
                self.assertNotIn("condição de novos clientes", response.lower())
                self.assertNotIn("link na hora", response.lower())
                self.assertNotIn("checkout.example.invalid", response)

    def test_fixed_closing_audio_marker_remains_available_with_selected_sales_model(self):
        result, dry_run, _, _ = self._run_text(
            "Pode me explicar isso?",
            sender="15555550816",
            initial_qualify={"step": "recomendacao", "pessoa": "cpf", "modelo": "t3"},
            llm_reply="[ÁUDIO: fechamento]",
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "sent")
        self.assertIn("condição de novos clientes", response.lower())

    def test_support_audio_pitch_only_uses_neutral_handoff_fallback(self):
        result, dry_run, _, _ = self._run_text(
            "Minha máquina não liga.",
            sender="15555550813",
            initial_qualify={"step": "posvenda"},
            llm_reply='[ÁUDIO: "Sugiro comprar uma T2 agora."]',
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "sent")
        self.assertIn("Entendi. Vou encaminhar seu caso para atendimento humano.", response)
        self.assertNotRegex(response.lower(), r"sugiro comprar|t2 agora|checkout\.example\.invalid")

    def test_llm_claim_sanitizer_runs_without_checkout_and_keeps_trusted_base_url(self):
        trusted_help_url = "https://support.example.invalid/guide"
        cases = (
            (
                "no_model",
                "Como funciona?",
                {},
                f"A opção é prática. Sai por 80 reais e pode ser parcelada em 12 vezes. Ajuda: {trusted_help_url}",
            ),
            (
                "doubt",
                "Como ela funciona?",
                {"step": "duvidas", "pessoa": "cpf", "modelo": "t3"},
                f"A T3 é prática no balcão. Sai por 80 reais e pode ser parcelada em 12 vezes. Ajuda: {trusted_help_url}",
            ),
        )
        for index, (name, prompt, state, llm_reply) in enumerate(cases):
            with self.subTest(mode=name):
                result, dry_run, _, _ = self._run_text(
                    prompt,
                    sender=f"1555555080{index + 2}",
                    initial_qualify=state,
                    llm_reply=llm_reply,
                    base_conhecimento=f"Ajuda oficial: {trusted_help_url}",
                )

                response = " ".join(text for _, text in dry_run.replies)
                self.assertEqual(result["status"], "sent")
                self.assertNotIn("80 reais", response.lower())
                self.assertNotIn("12 vezes", response.lower())
                self.assertIn(trusted_help_url, response)
                if name == "doubt":
                    self.assertIn("A T3 é prática no balcão.", response)
                    self.assertNotIn("checkout.example.invalid", response)

    def test_llm_without_model_removes_unconfigured_url(self):
        unconfigured_link = "https://attacker.example.invalid/track"
        result, dry_run, _, _ = self._run_text(
            "Como funciona?",
            sender="15555550787",
            initial_qualify={},
            llm_reply=f"Posso explicar como funciona. Consulte também {unconfigured_link}",
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "sent")
        self.assertIn("Posso explicar como funciona.", response)
        self.assertNotIn(unconfigured_link, response)

    def test_llm_without_model_removes_bare_unconfigured_url(self):
        unconfigured_link = "attacker.example.invalid/track"
        result, dry_run, _, _ = self._run_text(
            "Como funciona?",
            sender="15555550793",
            initial_qualify={},
            llm_reply=f"Posso explicar como funciona. Consulte {unconfigured_link}",
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "sent")
        self.assertNotIn(unconfigured_link, response)

    def test_llm_doubt_mode_removes_unconfigured_url_without_adding_checkout(self):
        unconfigured_link = "https://attacker.example.invalid/track"
        result, dry_run, _, _ = self._run_text(
            "Como ela funciona?",
            sender="15555550788",
            initial_qualify={"step": "duvidas", "pessoa": "cpf", "modelo": "t3"},
            llm_reply=f"A T3 pode ajudar nesse caso. Veja {unconfigured_link}",
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "sent")
        self.assertIn("A T3 pode ajudar nesse caso.", response)
        self.assertNotIn(unconfigured_link, response)
        self.assertNotIn("checkout.example.invalid", response)

    def test_deliver_offer_uses_black_link_for_eligible_cnpj(self):
        for index, message, status in (
            (0, "Quanto custa a T3?", "hot_preco_modelo"),
            (1, "Quero comprar a T3", "hot_compra_modelo"),
        ):
            with self.subTest(route=status):
                result, dry_run, _, _ = self._run_text(
                    message,
                    sender=f"1555555078{index}9",
                    initial_qualify={
                        "step": "recomendacao", "pessoa": "cnpj", "has_active_ton": False,
                        "continue_as_cpf": False, "volume": "30k_plus",
                    },
                )

                response = " ".join(text for _, text in dry_run.replies)
                self.assertEqual(result["status"], status)
                self.assertIn("https://checkout.example.invalid/black/t3", response)
                self.assertNotIn("https://checkout.example.invalid/mega-plus/t3", response)
                self.assertIn("Não tenho um snapshot promocional confirmado para T3", response)
                self.assertNotIn("72% OFF", response)
                self.assertNotIn("cupom 20%", response.lower())
                self.assertEqual(response.count("https://checkout.example.invalid/"), 1)
                if status == "hot_preco_modelo":
                    self.assertIn("R$ 108,00", response)

    def test_black_checkout_uses_complete_plan_specific_snapshot_when_present(self):
        models = self._checkout_test_models()
        t3 = next(model for model in models if model["id"] == "t3")
        black_snapshot = dict(t3["promocao"])
        black_snapshot["cupom"] = None
        t3["promocao_por_plano"] = {"black": black_snapshot}

        result, dry_run, _, _ = self._run_text(
            "Quanto custa a T3?",
            sender="15555550794",
            initial_qualify={
                "step": "recomendacao", "pessoa": "cnpj", "has_active_ton": False,
                "continue_as_cpf": False, "volume": "30k_plus",
            },
            models=models,
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "hot_preco_modelo")
        self.assertIn("https://checkout.example.invalid/black/t3", response)
        self.assertIn("72% OFF", response)
        self.assertIn("sem cupom extra", response.lower())
        self.assertNotIn("cupom 20%", response.lower())
        self.assertEqual(response.count("https://checkout.example.invalid/"), 1)

    def test_cnpj_black_recommendation_without_plan_snapshot_uses_cautious_copy(self):
        result, dry_run, _, _ = self._run_button(
            "cnpj_tem_ton_nao",
            sender="15555550795",
            initial_qualify={
                "step": "cnpj_ton", "pessoa": "cnpj", "has_active_ton": False,
                "continue_as_cpf": False, "volume": "30k_plus",
                "comprovante": "impresso",
            },
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "btn_recomendacao")
        self.assertIn("Não tenho um snapshot promocional confirmado para T3", response)
        self.assertNotIn("72% OFF", response)
        self.assertNotIn("cupom 20%", response.lower())
        self.assertNotIn("checkout.example.invalid", response)

    def test_link_resend_reselects_link_for_current_cnpj_volume(self):
        sender = "15555550792"
        _, _, _, main = self._run_text(
            "Quero comprar a T3",
            sender=sender,
            initial_qualify={
                "step": "recomendacao", "pessoa": "cnpj", "has_active_ton": False,
                "continue_as_cpf": False, "volume": "ate_10k",
            },
        )
        for message in main.HISTORY[sender]:
            if message.get("role") == "assistant":
                message["content"] = "Link anterior: https://checkout.example.invalid/mega-plus/t3"
        main.QUALIFY[sender]["modelo"] = ""
        main.QUALIFY[sender]["volume"] = "30k_plus"

        result, dry_run, _, _ = self._run_text(
            "Me manda o link",
            sender=sender,
            reset_state=False,
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "hot_link_reenvio")
        self.assertIn("https://checkout.example.invalid/black/t3", response)
        self.assertNotIn("https://checkout.example.invalid/mega-plus/t3", response)
        self.assertEqual(response.count("https://checkout.example.invalid/"), 1)

    def test_black_checkout_counts_as_offered_and_is_not_reattached_to_followup(self):
        sender = "15555550804"
        first, first_run, _, main = self._run_text(
            "Quero comprar a T3",
            sender=sender,
            initial_qualify={
                "step": "recomendacao", "pessoa": "cnpj", "has_active_ton": False,
                "continue_as_cpf": False, "volume": "30k_plus",
            },
        )
        first_response = " ".join(text for _, text in first_run.replies)
        self.assertEqual(first["status"], "hot_compra_modelo")
        self.assertIn("https://checkout.example.invalid/black/t3", first_response)
        self.assertTrue(
            main._already_offered(main.HISTORY[sender], self._checkout_test_models())
        )

        followup, followup_run, _, _ = self._run_text(
            "Pode me explicar como funciona?",
            sender=sender,
            reset_state=False,
            llm_reply="A T3 é prática para o balcão.",
        )

        followup_response = " ".join(text for _, text in followup_run.replies)
        self.assertEqual(followup["status"], "sent")
        self.assertIn("A T3 é prática para o balcão.", followup_response)
        self.assertNotIn("checkout.example.invalid", followup_response)

    def test_followup_requires_current_checkout_and_suppresses_handoff_or_opt_out(self):
        from admin.tests.whatsapp_test_harness import _load_main

        main = _load_main()
        phone = "15555550790"
        offer_ts = 1_800_000_000.0
        messages = [
            {"role": "user", "ts": offer_ts - 10, "content": "Pode mandar o link"},
            {
                "role": "assistant", "ts": offer_ts,
                "content": "Confira a condição no carrinho: https://checkout.example.invalid/black/t3",
            },
        ]
        cfg = {
            "modelos": [
                {
                    "id": "t3",
                    "link": "https://checkout.example.invalid/mega-plus/t3",
                    "link_black": "https://checkout.example.invalid/black/t3",
                },
            ],
        }
        with (
            patch.object(main, "_history_get", return_value=messages),
            patch.object(main, "_load", return_value=cfg),
            patch.object(main, "_q_get", return_value={"step": "recomendacao"}),
            patch.object(main, "_is_blocked", return_value=False),
            patch.object(main.time, "time", return_value=offer_ts + 30 * 60),
        ):
            self.assertTrue(main._needs_purchase_followup(phone))

        with (
            patch.object(main, "_history_get", return_value=messages),
            patch.object(main, "_load", return_value=cfg),
            patch.object(main, "_q_get", return_value={"step": "handoff"}),
            patch.object(main, "_is_blocked", return_value=False),
            patch.object(main.time, "time", return_value=offer_ts + 30 * 60),
        ):
            self.assertFalse(main._needs_purchase_followup(phone))

        with (
            patch.object(main, "_history_get", return_value=messages),
            patch.object(main, "_load", return_value=cfg),
            patch.object(main, "_q_get", return_value={"step": "recomendacao"}),
            patch.object(main, "_is_blocked", return_value=True),
            patch.object(main.time, "time", return_value=offer_ts + 30 * 60),
        ):
            self.assertFalse(main._needs_purchase_followup(phone))

        no_link_history = [
            {"role": "user", "ts": offer_ts - 10, "content": "Pode mandar"},
            {
                "role": "assistant", "ts": offer_ts,
                "content": "Você já fez a compra? Assim te mando o link do app.",
            },
        ]
        with (
            patch.object(main, "_history_get", return_value=no_link_history),
            patch.object(main, "_load", return_value=cfg),
            patch.object(main, "_q_get", return_value={"step": "recomendacao"}),
            patch.object(main, "_is_blocked", return_value=False),
            patch.object(main.time, "time", return_value=offer_ts + 30 * 60),
        ):
            self.assertFalse(main._needs_purchase_followup(phone))

    def test_followup_uses_last_real_checkout_offer_before_assistant_button_placeholder(self):
        from admin.tests.whatsapp_test_harness import _load_main

        main = _load_main()
        phone = "15555550792"
        offer_ts = 1_800_000_000.0
        messages = [
            {"role": "user", "ts": offer_ts - 10, "content": "Pode mandar o link"},
            {
                "role": "assistant", "ts": offer_ts,
                "content": "Confira a condição: https://checkout.example.invalid/mega-plus/t1",
            },
            {"role": "assistant", "ts": offer_ts + 1, "content": "Mais alguma dúvida?"},
        ]
        cfg = {"modelos": [{"id": "t1", "link": "https://checkout.example.invalid/mega-plus/t1"}]}
        with (
            patch.object(main, "_history_get", return_value=messages),
            patch.object(main, "_load", return_value=cfg),
            patch.object(main, "_q_get", return_value={"step": "recomendacao"}),
            patch.object(main, "_is_blocked", return_value=False),
            patch.object(main.time, "time", return_value=offer_ts + 30 * 60),
            patch.dict(main.FOLLOWUPS, {}, clear=True),
        ):
            self.assertTrue(main._needs_purchase_followup(phone))

    def test_followup_is_suppressed_by_customer_reply_after_checkout_even_with_assistant_bubble(self):
        from admin.tests.whatsapp_test_harness import _load_main

        main = _load_main()
        phone = "15555550793"
        offer_ts = 1_800_000_000.0
        messages = [
            {"role": "user", "ts": offer_ts - 10, "content": "Pode mandar o link"},
            {
                "role": "assistant", "ts": offer_ts,
                "content": "Confira a condição: https://checkout.example.invalid/black/t1",
            },
            {"role": "user", "ts": offer_ts + 60, "content": "Vou olhar, obrigado"},
            {"role": "assistant", "ts": offer_ts + 61, "content": "Mais alguma dúvida?"},
        ]
        cfg = {
            "modelos": [{
                "id": "t1", "link": "https://checkout.example.invalid/mega-plus/t1",
                "link_black": "https://checkout.example.invalid/black/t1",
            }],
        }
        with (
            patch.object(main, "_history_get", return_value=messages),
            patch.object(main, "_load", return_value=cfg),
            patch.object(main, "_q_get", return_value={"step": "recomendacao"}),
            patch.object(main, "_is_blocked", return_value=False),
            patch.object(main.time, "time", return_value=offer_ts + 30 * 60),
            patch.dict(main.FOLLOWUPS, {}, clear=True),
        ):
            self.assertFalse(main._needs_purchase_followup(phone))

    def test_duvidas_button_after_checkout_is_customer_activity_and_suppresses_followup(self):
        from admin.tests.whatsapp_test_harness import _load_main

        main = _load_main()
        sender = "15555550796"
        now_ts = main.time.time()
        offer_ts = now_ts - 31 * 60
        checkout_link = "https://checkout.example.invalid/mega-plus/t3"
        config = {"modelos": [{"id": "t3", "link": checkout_link}]}
        with (
            patch.dict(
                main.HISTORY,
                {
                    sender: [
                        {"role": "user", "ts": offer_ts - 10, "content": "Pode mandar o link"},
                        {
                            "role": "assistant",
                            "ts": offer_ts,
                            "content": f"Confira a condição: {checkout_link}",
                        },
                    ],
                },
                clear=True,
            ),
            patch.dict(main.QUALIFY, {sender: {"step": "recomendacao", "modelo": "t3"}}, clear=True),
            patch.dict(main.FOLLOWUPS, {}, clear=True),
            patch.object(main.time, "time", return_value=now_ts),
        ):
            result, _, _, _ = self._run_button(
                "duvidas",
                sender=sender,
                initial_qualify={"step": "recomendacao", "modelo": "t3"},
                reset_state=False,
            )

            self.assertEqual(result["status"], "btn_duvidas")
            history = main._history_get(sender)
            self.assertTrue(
                any(
                    event.get("role") == "user"
                    and event.get("content") == "[botão] tirar dúvidas"
                    for event in history
                ),
                "the interactive doubt click must be recorded as customer activity",
            )
            with patch.object(main, "_load", return_value=config):
                self.assertFalse(main._needs_purchase_followup(sender))

    def test_followup_is_idempotent_after_touch_when_assistant_bubbles_follow_checkout(self):
        from admin.tests.whatsapp_test_harness import _load_main

        main = _load_main()
        phone = "15555550794"
        offer_ts = 1_800_000_000.0
        messages = [
            {"role": "user", "ts": offer_ts - 10, "content": "Pode mandar o link"},
            {
                "role": "assistant", "ts": offer_ts,
                "content": "Confira a condição: https://checkout.example.invalid/mega-plus/t1",
            },
            {"role": "assistant", "ts": offer_ts + 1, "content": "Mais alguma dúvida?"},
            {"role": "assistant", "ts": offer_ts + 2, "content": "Escolha uma opção abaixo."},
        ]
        cfg = {"modelos": [{"id": "t1", "link": "https://checkout.example.invalid/mega-plus/t1"}]}
        with (
            patch.object(main, "_history_get", return_value=messages),
            patch.object(main, "_load", return_value=cfg),
            patch.object(main, "_q_get", return_value={"step": "recomendacao"}),
            patch.object(main, "_is_blocked", return_value=False),
            patch.object(main.time, "time", return_value=offer_ts + 30 * 60),
            patch.dict(main.FOLLOWUPS, {}, clear=True),
        ):
            self.assertTrue(main._needs_purchase_followup(phone))
            main.FOLLOWUPS[phone] = {"t1": offer_ts}
            self.assertFalse(main._needs_purchase_followup(phone))

    def test_followup_persists_real_checkout_offer_timestamp_after_assistant_placeholder(self):
        from admin.tests.whatsapp_test_harness import _load_main

        class StopFollowupLoop(Exception):
            pass

        main = _load_main()
        phone = "15555550795"
        offer_ts = 1_800_000_000.0
        now_ts = offer_ts + 30 * 60
        messages = [
            {"role": "user", "ts": offer_ts - 10, "content": "Pode mandar o link"},
            {
                "role": "assistant", "ts": offer_ts,
                "content": "Confira a condição: https://checkout.example.invalid/mega-plus/t1",
            },
            {"role": "assistant", "ts": offer_ts + 1, "content": "Mais alguma dúvida?"},
        ]
        cfg = {"modelos": [{"id": "t1", "link": "https://checkout.example.invalid/mega-plus/t1"}]}

        def append_followup(_phone, role, content):
            messages.append({"role": role, "content": content, "ts": now_ts})

        with (
            patch.object(main, "HISTORY", {phone: messages}),
            patch.object(main, "_history_get", return_value=messages),
            patch.object(main, "_history_append", side_effect=append_followup),
            patch.object(main, "_load", return_value=cfg),
            patch.object(main, "_q_get", return_value={"step": "recomendacao"}),
            patch.object(main, "_is_blocked", return_value=False),
            patch.object(main, "_send_reply"),
            patch.object(main, "_ctx_set"),
            patch.object(main, "_followups_save"),
            patch.object(main.time, "time", return_value=now_ts),
            patch.object(main.time, "sleep", side_effect=[None, StopFollowupLoop]),
            patch.dict(main.FOLLOWUPS, {}, clear=True),
        ):
            with self.assertRaises(StopFollowupLoop):
                main._followup_loop()

            self.assertEqual(main.FOLLOWUPS[phone], {"t1": offer_ts})

    def test_app_os_prompt_follows_declared_purchase_not_checkout_click(self):
        sender = "15555550791"
        checkout, checkout_run, state, _ = self._run_text(
            "Quero comprar a T3",
            sender=sender,
            initial_qualify={"step": "recomendacao", "pessoa": "cpf"},
        )
        checkout_copy = " ".join(text for _, text in checkout_run.replies)
        self.assertEqual(checkout["status"], "hot_compra_modelo")
        self.assertNotRegex(checkout_copy, r"Android ou iPhone|Seu celular é")
        self.assertNotEqual(state.get("step"), "app_os")

        click, click_run, state, _ = self._run_text(
            "Já cliquei no checkout",
            sender=sender,
            reset_state=False,
        )
        click_copy = " ".join(text for _, text in click_run.replies)
        self.assertNotEqual(click.get("status"), "compra_confirmada")
        self.assertNotIn("Android ou iPhone", click_copy)
        self.assertNotEqual(state.get("step"), "app_os")

        purchased, purchase_run, state, _ = self._run_text(
            "Comprei, finalizei o pedido",
            sender=sender,
            reset_state=False,
        )
        purchase_copy = " ".join(
            [text for _, text in purchase_run.replies]
            + [text for _, text, _ in purchase_run.buttons]
        )
        self.assertEqual(purchased["status"], "compra_confirmada")
        self.assertIn("Android ou iPhone", purchase_copy)
        self.assertEqual(state.get("step"), "app_os")

        app, app_run, state, _ = self._run_text(
            "Android",
            sender=sender,
            reset_state=False,
        )
        app_copy = " ".join(text for _, text in app_run.replies)
        self.assertEqual(app["status"], "app_link")
        self.assertIn("https://example.invalid/app", app_copy)
        self.assertEqual(state.get("step"), "posvenda")

    def test_os_buttons_send_app_link_only_after_purchase_confirmation_state(self):
        app_url = "https://example.invalid/app"
        for initial_state in ({}, {"step": "duvidas", "pessoa": "cpf"}):
            for button_id in ("os_android", "os_iphone"):
                with self.subTest(state=initial_state, button=button_id):
                    result, dry_run, state, _ = self._run_button(
                        button_id,
                        sender=f"155555508{button_id[-1]}{len(initial_state)}",
                        initial_qualify=dict(initial_state),
                    )
                    visible = " ".join(text for _, text in dry_run.replies)
                    self.assertEqual(result["status"], "os_button_requires_purchase")
                    self.assertNotIn(app_url, visible)
                    self.assertIn("compra", visible.lower())
                    self.assertEqual(state.get("step"), initial_state.get("step"))

        for button_id in ("os_android", "os_iphone"):
            with self.subTest(state="app_os", button=button_id):
                result, dry_run, state, _ = self._run_button(
                    button_id,
                    sender=f"155555509{button_id[-1]}",
                    initial_qualify={"step": "app_os", "purchase_confirmed": True},
                )
                visible = " ".join(text for _, text in dry_run.replies)
                self.assertEqual(result["status"], "app_link_btn")
                self.assertIn(app_url, visible)
                self.assertEqual(state.get("step"), "posvenda")

    def test_incomplete_model_promo_snapshot_uses_cautious_fallback(self):
        incomplete_fields = (
            ("cupom", None),
            ("desconto", ""),
            ("preco_observado", ""),
            ("observado_em", None),
            ("observado_em", "31/02/2026"),
            ("cupom", "10%"),
            ("desconto", "preço especial"),
            ("preco_observado", "preço promocional"),
        )
        for index, (field, value) in enumerate(incomplete_fields):
            with self.subTest(field=field):
                models = [model for model in self._checkout_test_models() if model["id"] == "t1"]
                if value is None:
                    models[0]["promocao"].pop(field)
                else:
                    models[0]["promocao"][field] = value
                result, dry_run, _, _ = self._run_text(
                    "Quero comprar a T1",
                    sender=f"1555555078{index + 1}",
                    initial_qualify={"step": "recomendacao", "pessoa": "cpf"},
                    models=models,
                    base_conhecimento="",
                )

                response = " ".join(text for _, text in dry_run.replies)
                self.assertEqual(result["status"], "hot_compra_modelo")
                self.assertIn("Não tenho um snapshot promocional confirmado para T1", response)
                self.assertNotIn("sem cupom extra", response.lower())
                self.assertNotIn("Snapshot afiliado observado em", response)
                self.assertNotIn("31/02/2026", response)

    def test_base_promo_row_requires_a_valid_dated_header(self):
        cases = (
            ("missing header", "- T1: 70% OFF; sem cupom extra; preço exibido: R$ 16,80"),
            (
                "invalid header",
                "Snapshot promocional afiliado observado em 32/13/2026\n"
                "- T1: 70% OFF; sem cupom extra; preço exibido: R$ 16,80",
            ),
            (
                "invalid observed cart price",
                "Snapshot promocional afiliado observado em 07/10/2026\n"
                "- T1: 70% OFF; sem cupom extra; preço exibido: R$ 1,,00",
            ),
        )
        for index, (name, base) in enumerate(cases):
            with self.subTest(name=name):
                models = [model for model in self._checkout_test_models() if model["id"] == "t1"]
                models[0].pop("promocao")
                result, dry_run, _, _ = self._run_text(
                    "Quero comprar a T1",
                    sender=f"15555550785{index}",
                    initial_qualify={"step": "recomendacao", "pessoa": "cpf"},
                    models=models,
                    base_conhecimento=base,
                )

                response = " ".join(text for _, text in dry_run.replies)
                self.assertEqual(result["status"], "hot_compra_modelo")
                self.assertIn("Não tenho um snapshot promocional confirmado para T1", response)
                self.assertNotIn("sem cupom extra", response.lower())
                self.assertNotIn("32/13/2026", response)
                self.assertNotIn("Snapshot afiliado observado em", response)

    def test_buy_button_without_selected_model_asks_one_cautious_question(self):
        result, dry_run, _, _ = self._run_button(
            "comprar", sender="15555550749", initial_qualify={}
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "btn_comprar")
        self.assertIn("Pode existir desconto", response)
        self.assertIn("Qual modelo você está considerando?", response)
        self.assertEqual(response.count("?"), 1)
        self.assertNotIn("http", response.lower())
        self.assertNotRegex(response, r"\b\d+%|cupom")

    def test_more_doubts_no_button_without_selected_model_asks_one_cautious_question(self):
        result, dry_run, _, _ = self._run_button(
            "mais_nao",
            sender="15555550751",
            initial_qualify={"step": "duvidas", "pessoa": "cpf"},
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "btn_mais_duvidas_sem_modelo")
        self.assertIn("Pode existir desconto", response)
        self.assertIn("Qual modelo você está considerando?", response)
        self.assertEqual(response.count("?"), 1)
        self.assertNotIn("http", response.lower())
        self.assertNotRegex(response, r"\b\d+%|cupom")

    def test_post_doubt_t1_button_copy_never_says_coupon_applied(self):
        from admin.tests.whatsapp_test_harness import FIXTURE_PATH, _load_main

        main = _load_main()
        cfg = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        modelo = next(model for model in self._checkout_test_models() if model["id"] == "t1")
        link = modelo["link"]
        cfg["modelos"] = [modelo]
        replies = []
        with (
            patch.object(main, "_guard_ton_checkout", return_value=None),
            patch.object(main, "_q_get", return_value={}),
            patch.object(main, "_history_get", return_value=[]),
            patch.object(main, "_history_append"),
            patch.object(main, "_send_reply", side_effect=lambda _cfg, _to, text: replies.append(text)),
            patch.object(main, "_log_event"),
            patch.object(main.time, "sleep", return_value=None),
        ):
            main._pos_duvida_link_sequence(cfg, "15555550750", modelo, link)

        response = " ".join(replies)
        self.assertIn("70% OFF", response)
        self.assertIn("sem cupom extra", response.lower())
        self.assertIn("R$ 16,80", response)
        self.assertNotIn("cupom 20%", response.lower())
        self.assertNotIn("cupom de desconto aplicado", response.lower())
        self.assertIn(link, response)

    def test_discount_reply_with_no_model_asks_one_question_without_a_link(self):
        result, dry_run, _, _ = self._run_text(
            "Tem desconto?",
            sender="15555550720",
            initial_qualify={},
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "hot_desconto")
        self.assertIn("Pode existir desconto na adesão", response)
        self.assertIn("Qual modelo você está considerando?", response)
        self.assertEqual(response.count("?"), 1)
        self.assertNotIn("http", response.lower())

    def test_discount_checkout_is_gated_for_active_cnpj(self):
        result, dry_run, _, _ = self._run_text(
            "Tem desconto?",
            sender="15555550721",
            initial_qualify={
                "step": "continuar_cpf", "pessoa": "cnpj", "has_active_ton": True,
                "continue_as_cpf": False, "modelo": "t3",
            },
        )

        self.assertEqual(result["status"], "btn_continuar_cpf")
        self.assertEqual(
            [button_id for button_id, _ in dry_run.buttons[-1][2]],
            ["continuar_cpf", "nao_seguir"],
        )
        self.assertFalse(any("checkout.example.invalid" in text for _, text in dry_run.replies))

    def test_discount_t1_with_pix_need_uses_existing_t2_checkout(self):
        result, dry_run, _, _ = self._run_text(
            "Tem desconto na T1? Preciso de Pix",
            sender="15555550727",
            initial_qualify={
                "step": "recomendacao", "pessoa": "cnpj", "has_active_ton": False,
                "continue_as_cpf": False, "volume": "30k_plus",
            },
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "hot_desconto")
        self.assertIn("A T1 não tem Pix na maquininha", response)
        self.assertIn("T2", response)
        self.assertIn("https://checkout.example.invalid/black/t2", response)
        self.assertNotIn("checkout.example.invalid/black/t1", response)
        self.assertNotIn("checkout.example.invalid/mega-plus/t1", response)

    def test_discount_t1_with_pix_need_and_no_t2_sends_no_checkout(self):
        only_t1 = [m for m in self._checkout_test_models() if m["id"] == "t1"]
        result, dry_run, _, _ = self._run_text(
            "Tem desconto na T1? Preciso de Pix",
            sender="15555550728",
            initial_qualify={"step": "recomendacao", "pessoa": "cpf"},
            models=only_t1,
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "hot_desconto")
        self.assertIn("A T1 não tem Pix na maquininha", response)
        self.assertNotIn("checkout.example.invalid", response)

    def test_price_objection_with_t1_pix_need_uses_t2_checkout(self):
        result, dry_run, _, _ = self._run_text(
            "Tá caro comprar a T1. Preciso de Pix",
            sender="15555550729",
            initial_qualify={
                "step": "recomendacao", "pessoa": "cnpj", "has_active_ton": False,
                "continue_as_cpf": False, "volume": "30k_plus",
            },
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "hot_preco_modelo")
        self.assertEqual(result["modelo"], "t2")
        self.assertIn("A T1 não tem Pix na maquininha", response)
        self.assertIn("https://checkout.example.invalid/black/t2", response)
        self.assertNotIn("https://checkout.example.invalid/mega-plus/t2", response)
        self.assertNotIn("checkout.example.invalid/black/t1", response)
        self.assertNotIn("checkout.example.invalid/mega-plus/t1", response)

    def test_tax_question_does_not_invent_installments_or_rate(self):
        result, dry_run, _, _ = self._run_text(
            "Qual é a taxa no crédito?",
            sender="15555550706",
            initial_qualify={},
            llm_reply="A taxa é 0,5% em 12x e ainda tem 5% de cashback.",
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "hot_taxa")
        self.assertRegex(response.lower(), r"taxa.*(varia|depende|confirm)")
        self.assertNotIn("0,5%", response)
        self.assertNotRegex(response.lower(), r"\b\d+x\b|parcel")

    def test_price_objection_does_not_claim_fixed_five_percent_or_cashback(self):
        result, dry_run, _, _ = self._run_text(
            "Tá caro. Tem desconto e cashback?",
            sender="15555550707",
            initial_qualify={"step": "recomendacao", "pessoa": "cpf", "modelo": "t3"},
            llm_reply="Sim, garanto 5% de desconto e cashback na compra.",
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "hot_preco_objecao")
        self.assertNotIn("5%", response)
        self.assertIn("Não consigo confirmar uma condição de cashback", response)
        self.assertIn("Pode existir desconto", response)
        self.assertIn("https://checkout.example.invalid/mega-plus/t3", response)
        self.assertNotIn("https://checkout.example.invalid/mega-plus/t2", response)

    def test_guarantee_answer_does_not_append_a_duplicate_checkout_question(self):
        result, dry_run, _, _ = self._run_text(
            "A T3 tem garantia?",
            sender="15555550708",
            initial_qualify={"step": "recomendacao", "pessoa": "cpf", "modelo": "t3"},
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "fast_garantia")
        self.assertIn("garantia vitalícia", response.lower())
        self.assertNotRegex(response.lower(), r"quer que eu.*link|quer receber.*link|posso te mandar.*link")

    def test_unsolicited_model_list_does_not_mention_tapton(self):
        result, dry_run, _, _ = self._run_text(
            "Quais modelos de maquininha vocês têm?",
            sender="15555550709",
            initial_qualify={},
            llm_reply="Temos T1, T2, T3, T3 Smart e TapTon.",
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "hot_catalogo_modelos")
        self.assertNotIn("TapTon", response)
        for model_name in ("T1", "T2", "T3", "T3 Smart"):
            self.assertIn(model_name, response)

    def test_support_intent_takes_precedence_over_embedded_purchase_request(self):
        result, dry_run, _, _ = self._run_text(
            "Já tenho maquininha, ela não liga. Quero comprar uma T2 se precisar.",
            sender="15555550710",
            initial_qualify={"step": "duvidas", "pessoa": "cpf"},
            llm_reply="Posso ajudar com o problema da máquina. O que aparece no visor?",
        )

        response = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "sent")
        self.assertNotIn("checkout.example.invalid", response)
        self.assertNotIn("T2", response)

    def test_queued_checkout_is_gated_if_cnpj_ton_becomes_active_before_dispatch(self):
        from admin.tests.whatsapp_test_harness import WhatsAppDryRun

        sender = "15555550711"
        pending_workers = []
        result, dry_run, _, main = self._run_text(
            "Pode me explicar melhor?",
            sender=sender,
            initial_qualify={"step": "recomendacao", "pessoa": "cpf", "modelo": "t3"},
            llm_reply="A T3 pode ser uma opção para você.",
            thread_runner=lambda target, *args, **kwargs: pending_workers.append(
                (target, args, kwargs)
            ),
        )

        self.assertEqual(result["status"], "sent")
        self.assertEqual(len(pending_workers), 1, "the worker should remain queued for the race test")
        main.QUALIFY[sender].update(
            {
                "step": "continuar_cpf",
                "pessoa": "cnpj",
                "has_active_ton": True,
                "continue_as_cpf": False,
            }
        )
        (worker, args, kwargs) = pending_workers[0]
        dry_run = WhatsAppDryRun()
        with (
            patch.object(
                main,
                "_send_reply",
                side_effect=lambda cfg, to, text: dry_run.send_text(to, text),
            ),
            patch.object(
                main,
                "_send_buttons",
                side_effect=lambda cfg, to, text, buttons: dry_run.send_buttons(to, text, buttons),
            ),
            patch.object(main, "_send_media", side_effect=AssertionError("checkout card must be gated")),
            patch.object(main, "_pausa", return_value=None),
        ):
            worker(*args, **kwargs)

        dispatched_text = " ".join(text for _, text in dry_run.replies)
        self.assertNotIn("checkout.example.invalid", dispatched_text)
        self.assertEqual(
            [button_id for _, _, buttons in dry_run.buttons for button_id, _ in buttons],
            ["continuar_cpf", "nao_seguir"],
        )

    def test_queued_checkout_rechecks_model_compatibility_before_dispatch(self):
        from admin.tests.whatsapp_test_harness import WhatsAppDryRun

        sender = "15555550865"
        pending_workers = []
        result, _, _, main = self._run_text(
            "Pode me explicar melhor?",
            sender=sender,
            initial_qualify={
                "step": "recomendacao", "pessoa": "cpf", "modelo": "t2",
                "comprovante": "digital",
            },
            llm_reply="A T2 combina com você.",
            thread_runner=lambda target, *args, **kwargs: pending_workers.append(
                (target, args, kwargs)
            ),
        )

        self.assertEqual(result["status"], "sent")
        self.assertEqual(len(pending_workers), 1)
        main.HISTORY[sender].append({
            "role": "user", "content": "Agora preciso de comprovante impresso",
            "ts": main.time.time() + 1,
        })
        worker, args, kwargs = pending_workers[0]
        dry_run = WhatsAppDryRun()
        with (
            patch.object(
                main, "_send_reply",
                side_effect=lambda cfg, to, text: dry_run.send_text(to, text),
            ),
            patch.object(
                main, "_send_buttons",
                side_effect=lambda cfg, to, text, buttons: dry_run.send_buttons(to, text, buttons),
            ),
            patch.object(main, "_pausa", return_value=None),
        ):
            worker(*args, **kwargs)

        response = " ".join(text for _, text in dry_run.replies)
        self.assertNotIn("A T2 combina com você", response)
        self.assertIn("não imprime comprovante", response.lower())
        self.assertEqual(
            self._checkout_urls(response),
            ["https://checkout.example.invalid/mega-plus/t3"],
        )

    def test_queued_checkout_is_gated_if_cnpj_ton_becomes_pending_before_dispatch(self):
        from admin.tests.whatsapp_test_harness import WhatsAppDryRun

        sender = "15555550714"
        pending_workers = []
        result, _, _, main = self._run_text(
            "Pode me explicar melhor?",
            sender=sender,
            initial_qualify={"step": "recomendacao", "pessoa": "cpf", "modelo": "t3"},
            llm_reply="A T3 pode ser uma opção para você.",
            thread_runner=lambda target, *args, **kwargs: pending_workers.append(
                (target, args, kwargs)
            ),
        )

        self.assertEqual(result["status"], "sent")
        main.QUALIFY[sender].update(
            {"step": "cnpj_ton", "pessoa": "cnpj", "has_active_ton": False,
             "continue_as_cpf": False}
        )
        worker, args, kwargs = pending_workers[0]
        dry_run = WhatsAppDryRun()
        with (
            patch.object(
                main, "_send_reply",
                side_effect=lambda cfg, to, text: dry_run.send_text(to, text),
            ),
            patch.object(
                main, "_send_buttons",
                side_effect=lambda cfg, to, text, buttons: dry_run.send_buttons(to, text, buttons),
            ),
            patch.object(main, "_pausa", return_value=None),
        ):
            worker(*args, **kwargs)

        self.assertNotIn(
            "checkout.example.invalid",
            " ".join(text for _, text in dry_run.replies),
        )
        self.assertEqual(
            [button_id for _, _, buttons in dry_run.buttons for button_id, _ in buttons],
            ["cnpj_tem_ton_sim", "cnpj_tem_ton_nao"],
        )

    def test_queued_offer_sends_no_photo_when_cnpj_becomes_active(self):
        from admin.tests.whatsapp_test_harness import WhatsAppDryRun

        sender = "15555550715"
        pending_workers = []
        result, _, _, main = self._run_text(
            "Pode me explicar melhor?",
            sender=sender,
            initial_qualify={"step": "duvidas", "pessoa": "cpf", "modelo": "t3"},
            llm_reply="A T3 tem recursos que podem ajudar no seu caso.",
            thread_runner=lambda target, *args, **kwargs: pending_workers.append(
                (target, args, kwargs)
            ),
            canal="baileys",
        )

        self.assertEqual(result["status"], "sent")
        main.QUALIFY[sender].update(
            {"step": "continuar_cpf", "pessoa": "cnpj", "has_active_ton": True,
             "continue_as_cpf": False}
        )
        worker, args, kwargs = pending_workers[0]
        dry_run = WhatsAppDryRun()
        media_calls = []
        with (
            patch.object(
                main, "_send_reply",
                side_effect=lambda cfg, to, text: dry_run.send_text(to, text),
            ),
            patch.object(
                main, "_send_buttons",
                side_effect=lambda cfg, to, text, buttons: dry_run.send_buttons(to, text, buttons),
            ),
            patch.object(main, "_send_media", side_effect=lambda *a, **kw: media_calls.append(a)),
            patch.object(main, "_pausa", return_value=None),
        ):
            worker(*args, **kwargs)

        self.assertTrue(dry_run.replies, "the ordinary text answer should still be sent")
        self.assertNotIn(
            "checkout.example.invalid",
            " ".join(text for _, text in dry_run.replies),
        )
        self.assertEqual(media_calls, [])
        self.assertEqual(
            [button_id for _, _, buttons in dry_run.buttons for button_id, _ in buttons],
            ["mais_sim", "mais_nao"],
        )

    def test_00_import_bootstrap_uses_temporary_database_and_test_admin_credentials(self):
        from admin.tests.whatsapp_test_harness import (
            FIXTURE_PATH,
            FakeRequest,
            WhatsAppDryRun,
            _load_main,
            run_webhook,
        )

        with TemporaryDirectory(prefix="whatsapp-bootstrap-regression-") as temp_dir:
            temporary_db = Path(temp_dir) / "bootstrap.sqlite"
            real_connect = sqlite3.connect
            connect_targets = []
            real_config_reads = []
            original_read_text = Path.read_text
            config_dir = Path(__file__).resolve().parents[1] / "config"

            def redirect_connect(database, *args, **kwargs):
                # Safety net for the RED run: even the broken harness writes
                # only to this temporary database while the test observes its
                # requested destination.
                connect_targets.append(Path(database))
                return real_connect(temporary_db, *args, **kwargs)

            def hide_real_config(path, *args, **kwargs):
                if path.parent.resolve() == config_dir.resolve() and path.suffix == ".json":
                    real_config_reads.append(path)
                    return original_read_text(FIXTURE_PATH, *args, **kwargs)
                return original_read_text(path, *args, **kwargs)

            bootstrap_stdout = io.StringIO()
            with patch.object(sqlite3, "connect", side_effect=redirect_connect):
                with patch.object(Path, "read_text", hide_real_config):
                    with patch.dict(
                        os.environ,
                        {
                            "ADMIN_EMAIL": "red-probe@invalid.example",
                            "ADMIN_PASSWORD": "red-probe-password",
                        },
                    ):
                        with patch(
                            "urllib.request.urlopen",
                            side_effect=AssertionError("network access blocked in regression test"),
                        ):
                            with redirect_stdout(bootstrap_stdout):
                                main = _load_main()

            self.assertTrue(connect_targets, "the app database bootstrap did not run")
            self.assertTrue(
                all(
                    Path(tempfile.gettempdir()) in path.parents
                    and path != config_dir / "users.db"
                    for path in connect_targets
                ),
                f"SQLite bootstrap requested a non-temporary path: {connect_targets}",
            )
            self.assertEqual(
                main.USERS_DB_PATH,
                connect_targets[0],
                "the imported app module retained its production users.db path",
            )
            self.assertFalse(real_config_reads, "bootstrap tried to read real admin/config JSON")
            self.assertEqual(bootstrap_stdout.getvalue(), "", "bootstrap output was not suppressed")

            connection = real_connect(temporary_db)
            try:
                seeded_email = connection.execute("SELECT email FROM users").fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(seeded_email, "whatsapp-dry-run@example.invalid")

            active_user_db_paths = []

            class ObserveUsersDbRequest(FakeRequest):
                async def json(self):
                    active_user_db_paths.append(main.USERS_DB_PATH)
                    return await super().json()

            result = run_webhook(ObserveUsersDbRequest({"object": "synthetic_unknown"}), WhatsAppDryRun())
            self.assertEqual(result["status"], "no_text")
            self.assertEqual(len(active_user_db_paths), 1)
            self.assertIn(Path(tempfile.gettempdir()), active_user_db_paths[0].parents)
            self.assertNotEqual(active_user_db_paths[0], config_dir / "users.db")

    def test_audio_url_is_transcribed_and_reply_is_captured_without_urlopen(self):
        from admin.tests.whatsapp_test_harness import (
            FakeRequest,
            WhatsAppDryRun,
            run_webhook,
        )

        sender = "15555550101"
        payload = {
            "event": "messages.upsert",
            "data": {
                "key": {
                    "remoteJid": f"{sender}@s.whatsapp.net",
                    "fromMe": False,
                    "id": "",
                },
                "message": {
                    "audioMessage": {
                        "url": "https://audio.test.invalid/synthetic.ogg",
                        "mimetype": "audio/ogg; codecs=opus",
                        "fileLength": 123,
                    }
                },
            },
        }
        dry_run = WhatsAppDryRun()

        with patch(
            "urllib.request.urlopen",
            side_effect=AssertionError("network access blocked in audio regression test"),
        ) as urlopen:
            with patch(
                "stt.transcribe_audio_url",
                return_value="Quero saber mais sobre as opções disponíveis.",
            ) as transcribe:
                result = run_webhook(FakeRequest(payload), dry_run)

        transcribe.assert_called_once_with("https://audio.test.invalid/synthetic.ogg")
        urlopen.assert_not_called()
        self.assertEqual(result["status"], "sent")
        self.assertTrue(dry_run.replies, "the transcribed audio reply was not captured")
        self.assertEqual(dry_run.replies[0][0], sender)

    def test_synthetic_webhook_captures_reply_and_buttons_without_meta_or_strata_calls(self):
        harness_path = Path(__file__).with_name("whatsapp_test_harness.py")
        self.assertTrue(
            harness_path.is_file(),
            "the reusable WhatsApp webhook dry-run harness has not been created",
        )

        from admin.tests.whatsapp_test_harness import (
            FakeRequest,
            WhatsAppDryRun,
            run_webhook,
        )

        sender = "15555550100"
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "TEST_WABA_ID",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": "TEST_PHONE_NUMBER_ID"},
                                "messages": [
                                    {
                                        "from": sender,
                                        "id": "wamid.TEST_MESSAGE_001",
                                        "timestamp": "1791321600",
                                        "type": "text",
                                        "text": {
                                            "body": (
                                                "Quero entender em detalhes as condições e diferenças "
                                                "disponíveis para escolher com segurança a opção mais "
                                                "adequada ao meu negócio e às necessidades de atendimento "
                                                "que expliquei anteriormente nesta conversa."
                                            )
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
        dry_run = WhatsAppDryRun()

        result = run_webhook(
            FakeRequest(payload),
            dry_run,
            initial_qualify={sender: {"step": "duvidas"}},
        )

        self.assertEqual(result["status"], "sent")
        self.assertTrue(dry_run.replies, "the webhook response was not captured")
        self.assertTrue(dry_run.buttons, "the follow-up buttons were not captured")
        self.assertEqual(dry_run.replies[0][0], sender)
        self.assertIn("A opção adequada", dry_run.replies[0][1])
        self.assertEqual(dry_run.buttons[0][0], sender)
        self.assertFalse(
            any(
                endpoint in url.lower()
                for event, url in dry_run.events
                if event == "http"
                for endpoint in ("graph.facebook.com", "strata")
            ),
            "the dry run attempted to call Meta or Strata",
        )

    def test_checkout_url_contract_rejects_extra_or_wrong_checkout_links(self):
        t3_link = "https://checkout.example.invalid/mega-plus/t3"
        t2_link = "https://checkout.example.invalid/mega-plus/t2"
        app_link = "https://example.invalid/app"

        with self.assertRaises(AssertionError):
            self._assert_checkout_url_contract(f"Link T3: {t3_link}\nOutro: {t2_link}", t3_link)
        with self.assertRaises(AssertionError):
            self._assert_checkout_url_contract(f"Link T2: {t2_link}", t3_link)

        self._assert_checkout_url_contract(f"App: {app_link}", app_link)
        with self.assertRaises(AssertionError):
            self._assert_checkout_url_contract(
                f"App: {app_link}\nCheckout indevido: {t3_link}", app_link
            )
        self._assert_checkout_url_contract("Sem checkout.", None)

    def test_tapton_guard_is_scoped_to_explicit_inquiries(self):
        with self.assertRaises(AssertionError):
            self._assert_tapton_scope(
                {"input": "Quais modelos?", "tapton_inquiry": False}, "Também temos TapTon."
            )

        self._assert_tapton_scope(
            {"input": "O que é TapTon?", "tapton_inquiry": True},
            "TapTon pode ser explicado com base na informação oficial disponível.",
        )
        self._assert_tapton_scope(
            {"input": "Quais modelos?", "tapton_inquiry": False}, "Temos T1, T2 e T3."
        )

    def test_support_guard_allows_model_names_and_harmless_denials(self):
        self._assert_support_pitch_absent(
            "Não sugiro comprar a T2 nem promover uma oferta; vamos focar no estorno."
        )
        with self.assertRaises(AssertionError):
            self._assert_support_pitch_absent("Aproveite a oferta e compre a T2 agora.")
        with self.assertRaises(AssertionError):
            self._assert_support_pitch_absent(
                "Fale com nossa equipe: https://checkout.example.invalid/mega-plus/t2"
            )

    def test_support_sanitizer_removes_positive_purchase_pitch_but_keeps_negation(self):
        positive, positive_run, _, _ = self._run_text(
            "Meu pagamento não caiu",
            sender="15555550901",
            initial_qualify={"step": "posvenda", "pessoa": "cpf"},
            llm_reply=(
                "Entendo o problema. Sugiro comprar uma T2 agora. "
                "Vamos verificar o pagamento que não caiu."
            ),
        )
        positive_copy = " ".join(text for _, text in positive_run.replies)
        self.assertEqual(positive["status"], "sent")
        self.assertIn("Entendo o problema.", positive_copy)
        self.assertIn("Vamos verificar o pagamento", positive_copy)
        self.assertNotIn("Sugiro comprar uma T2 agora", positive_copy)
        self.assertEqual(self._checkout_urls(positive_copy), [])

        negated, negated_run, _, _ = self._run_text(
            "Meu pagamento não caiu",
            sender="15555550902",
            initial_qualify={"step": "posvenda", "pessoa": "cpf"},
            llm_reply=(
                "Não sugiro comprar uma T2 agora. Sugiro não comprar uma T3 neste momento. "
                "Vamos verificar o pagamento que não caiu."
            ),
        )
        negated_copy = " ".join(text for _, text in negated_run.replies)
        self.assertEqual(negated["status"], "sent")
        self.assertIn("Não sugiro comprar uma T2 agora.", negated_copy)
        self.assertIn("Sugiro não comprar uma T3 neste momento.", negated_copy)

        handoff, handoff_run, handoff_state, _ = self._run_text(
            "Meu pagamento não caiu",
            sender="15555550904",
            initial_qualify={"step": "handoff", "pessoa": "cpf"},
            llm_reply="Entendo. Sugiro comprar uma T2 agora. Vou encaminhar seu caso.",
        )
        # Pós-handoff o bot fica em silêncio: nada é enviado, só registra.
        self.assertEqual(handoff["status"], "silenced_handoff")
        self.assertEqual(handoff_run.replies, [])
        self.assertEqual(handoff_run.buttons, [])
        self.assertEqual(handoff_state.get("step"), "handoff")

    def test_support_purchase_pitch_sanitizer_does_not_change_sales_path(self):
        result, dry_run, _, _ = self._run_text(
            "Pode me explicar melhor?",
            sender="15555550903",
            initial_qualify={"step": "recomendacao", "pessoa": "cpf"},
            llm_reply="Sugiro comprar uma T2 agora.",
        )
        visible = " ".join(text for _, text in dry_run.replies)
        self.assertEqual(result["status"], "sent")
        self.assertIn("Sugiro comprar uma T2 agora.", visible)

    def test_greeting_goes_straight_to_business_type(self):
        result, dry_run, state, _ = self._run_text(
            "Oi",
            sender="15555550910",
            initial_qualify={},
        )
        self.assertEqual(result["status"], "greeted_tipo")
        self.assertEqual(state.get("step"), "tipo")
        button_ids = [
            button_id
            for _, _, buttons in dry_run.buttons
            for button_id, _ in buttons
        ]
        self.assertEqual(button_ids, ["loja_fisica", "servicos_externos", "outro"])
        visible = "\n".join([text for _, text in dry_run.replies] + [text for _, text, _ in dry_run.buttons])
        self.assertIn("tipo de negócio", visible)
        self.assertNotIn("atendente", visible.lower())

    def test_handoff_text_stays_silent(self):
        result, dry_run, state, _ = self._run_text(
            "Qual o prazo de entrega?",
            sender="15555550911",
            initial_qualify={"step": "handoff", "pessoa": "cpf"},
            llm_reply="O prazo é de 3 a 7 dias úteis.",
        )
        self.assertEqual(result["status"], "silenced_handoff")
        self.assertEqual(dry_run.replies, [])
        self.assertEqual(dry_run.buttons, [])
        self.assertEqual(state.get("step"), "handoff")

    def test_handoff_button_stays_silent(self):
        result, dry_run, state, _ = self._run_button(
            "duvidas",
            sender="15555550912",
            initial_qualify={"step": "handoff", "pessoa": "cpf"},
        )
        self.assertEqual(result["status"], "silenced_handoff")
        self.assertEqual(dry_run.replies, [])
        self.assertEqual(dry_run.buttons, [])

    def test_explicit_attendant_request_does_not_escalate(self):
        result, dry_run, state, _ = self._run_text(
            "Quero falar com atendente",
            sender="15555550913",
            initial_qualify={},
            llm_reply="Claro! Me conta sua dúvida que eu te ajudo.",
        )
        self.assertNotEqual(result["status"], "escalado")
        self.assertNotIn("nossa equipe", "\n".join(text for _, text in dry_run.replies).lower())
        self.assertNotEqual(state.get("step"), "handoff")

    def test_reclamacao_does_not_auto_escalate(self):
        result, dry_run, _, _ = self._run_text(
            "Quero cancelar minha compra",
            sender="15555550914",
            initial_qualify={},
            llm_reply="Entendo. Me conta o que aconteceu com seu pedido?",
        )
        self.assertEqual(result["status"], "sent")

    def test_uncovered_doubt_reply_marks_handoff(self):
        result, dry_run, state, _ = self._run_text(
            "Vocês têm maquininha que imprime via bluetooth?",
            sender="15555550915",
            initial_qualify={"step": "duvidas", "pessoa": "cpf"},
            llm_reply="Não tenho essa informação na base. Vou te encaminhar para atendimento humano para confirmar.",
        )
        self.assertEqual(result["status"], "sent")
        self.assertEqual(state.get("step"), "handoff")

    def test_panel_send_marks_handoff_so_bot_stays_quiet(self):
        import asyncio
        from unittest.mock import patch
        from admin.tests.whatsapp_test_harness import FakeRequest, _load_main

        main = _load_main()
        sender = "15555550920"
        main.QUALIFY[sender] = {"step": "recomendacao", "pessoa": "cpf"}
        with (
            patch.object(main, "_current_user", return_value={"id": 1}),
            patch.object(main, "_load", return_value={"canal": "baileys"}),
            patch.object(main, "_is_blocked", return_value=False),
            patch.object(main, "_send_reply", return_value=None),
        ):
            response = asyncio.run(
                main.send_conversation_message(sender, FakeRequest({"text": "Olá, sou o André"}))
            )
        self.assertEqual(response, {"ok": True})
        self.assertEqual(main._q_get(sender).get("step"), "handoff")

    def test_handoff_failed_audio_stays_silent(self):
        from admin.tests.whatsapp_test_harness import (
            FakeRequest,
            WhatsAppDryRun,
            _load_main,
            run_webhook,
        )

        main = _load_main()
        sender = "15555550921"
        payload = {
            "event": "messages.upsert",
            "data": {
                "key": {
                    "remoteJid": f"{sender}@s.whatsapp.net",
                    "id": "audio-handoff-1",
                    "fromMe": False,
                },
                "message": {"audioMessage": {"url": "https://example.invalid/audio.ogg"}},
            },
        }
        dry_run = WhatsAppDryRun()
        with patch("stt.transcribe_audio_url", side_effect=RuntimeError("sem audio")):
            result = run_webhook(
                FakeRequest(payload),
                dry_run,
                initial_qualify={sender: {"step": "handoff", "pessoa": "cpf"}},
            )
        self.assertEqual(result["status"], "no_text")
        self.assertEqual(dry_run.replies, [])
        self.assertEqual(dry_run.buttons, [])

    def test_data_driven_funnel_conversations(self):
        from admin.tests.whatsapp_test_harness import _load_main
        from admin.followup_policy import purchase_check_due

        fixture_path = Path(__file__).resolve().parent / "fixtures" / "funnel_cases.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        scenarios = fixture["cases"]
        self.assertEqual(len(scenarios), 220, "fixture must contain exactly 220 scenarios")
        self.assertEqual(len({scenario["id"] for scenario in scenarios}), 220)

        configured_urls = {
            f"https://checkout.example.invalid/{plan}/{model}"
            for plan in ("mega-plus", "black")
            for model in ("t1", "t2", "t3", "t3-smart")
        } | {"https://example.invalid/app"}
        collected_outputs = []
        main = _load_main()

        for index, scenario in enumerate(scenarios):
            sender = f"1555555{index:04d}"
            state = dict(scenario.get("state") or {})
            for step_index, step in enumerate(scenario["steps"]):
                with self.subTest(case=scenario["id"], step=step_index, category=scenario["category"]):
                    if step["kind"] == "followup":
                        if step.get("blocked"):
                            self.assertTrue(main._is_blocked(sender))
                            due = main._needs_purchase_followup(sender)
                        else:
                            due = purchase_check_due(
                                offer_ts=step["offer_ts"],
                                latest_customer_ts=step["customer_ts"],
                                now_ts=step["now_ts"],
                                already_purchased=step.get("purchased", False),
                                already_sent=step.get("already_sent", False),
                            )
                        self.assertIs(due, step["due"])
                        continue

                    reset_state = step_index == 0
                    if step["kind"] == "button":
                        result, dry_run, state, main = self._run_button(
                            step["input"],
                            sender=sender,
                            initial_qualify=state,
                            reset_state=reset_state,
                        )
                    else:
                        result, dry_run, state, main = self._run_text(
                            step["input"],
                            sender=sender,
                            initial_qualify=state,
                            llm_reply=step.get("llm_reply"),
                            reset_state=reset_state,
                        )

                    self.assertEqual(result["status"], step["status"])
                    if "step" in step:
                        self.assertEqual(state.get("step"), step["step"])

                    reply_texts = [text for _, text in dry_run.replies]
                    button_texts = [text for _, text, _ in dry_run.buttons]
                    visible_text = "\n".join(reply_texts + button_texts)
                    collected_outputs.extend(
                        (scenario["category"], text)
                        for text in reply_texts + button_texts
                    )

                    self.assertIn(step.get("copy", ""), visible_text)
                    self._assert_tapton_scope(step, visible_text)
                    for expected_copy in step.get("also_contains", []):
                        self.assertIn(expected_copy, visible_text)
                    actual_button_ids = [
                        button_id
                        for _, _, buttons in dry_run.buttons
                        for button_id, _ in buttons
                    ]
                    self.assertEqual(actual_button_ids, step.get("buttons", []))
                    if "labels" in step:
                        actual_button_labels = [
                            label
                            for _, _, buttons in dry_run.buttons
                            for _, label in buttons
                        ]
                        self.assertEqual(actual_button_labels, step["labels"])
                    if step.get("url"):
                        self.assertIn(step["url"], visible_text)
                        self.assertEqual(visible_text.count(step["url"]), 1)
                        if "/mega-plus/" in step["url"]:
                            model_id = step["url"].rsplit("/", 1)[-1]
                            snapshots = {
                                "t1": ("70% OFF", "R$ 16,80", None),
                                "t2": ("72% OFF", "R$ 39,90", "cupom 20%"),
                                "t3": ("72% OFF", "R$ 86,40", "cupom 20%"),
                                "t3-smart": ("71% OFF", "R$ 153,50", "cupom 20%"),
                            }
                            discount, observed_price, coupon = snapshots[model_id]
                            self.assertIn(discount, visible_text)
                            self.assertIn(observed_price, visible_text)
                            self.assertIn("07/10/2026", visible_text)
                            self.assertRegex(visible_text.lower(), r"promo.{0,80}podem mudar")
                            self.assertRegex(visible_text.lower(), r"carrinho.*(?:confirme|final)")
                            if coupon:
                                self.assertIn(coupon, visible_text.lower())
                            else:
                                self.assertIn("sem cupom extra", visible_text.lower())
                                self.assertNotIn("cupom 20%", visible_text.lower())
                        elif "/black/" in step["url"]:
                            self.assertIn("Não tenho um snapshot promocional confirmado", visible_text)
                            self.assertNotRegex(visible_text, r"\b(?:70|71|72)% OFF|cupom 20%")
                    self._assert_checkout_url_contract(visible_text, step.get("url"))
                    if step.get("no_output"):
                        self.assertEqual(dry_run.replies, [])
                        self.assertEqual(dry_run.buttons, [])
                    for forbidden in step.get("forbidden", []):
                        self.assertNotIn(forbidden.lower(), visible_text.lower())

                    # An explicit canned response is the only permitted LLM path;
                    # every HTTP boundary remains the test harness stub.
                    for event, url in dry_run.events:
                        if event == "http":
                            self.assertIsNotNone(
                                step.get("llm_reply"),
                                "LLM fallback scenarios must use an explicit canned reply",
                            )
                            self.assertIn(".test.invalid", url)
                            self.assertNotIn("graph.facebook.com", url.lower())
                            self.assertNotIn("strata", url.lower())

        # Cross-case guardrails validate emitted text, not just HTTP status codes.
        for category, text in collected_outputs:
            with self.subTest(global_category=category, output=text[:80]):
                urls = [url.rstrip(".,!?)]}") for url in re.findall(r"https?://[^\s<>]+", text)]
                self.assertTrue(set(urls).issubset(configured_urls), f"unconfigured URL in output: {urls}")
                self.assertNotRegex(text.lower(), r"\b(?:0,57|0\.57|0,5|0\.5)%")
                self.assertNotRegex(text.lower(), r"\b5%\s*(?:off|de desconto)")
                safe_percentages = re.sub(
                    r"(?i)\b(?:70|71|72)%\s*OFF\b|\bcupom\s+20%",
                    "",
                    text,
                )
                safe_percentages = re.sub(r"\b(?:0%|0,49%|0,74%)", "", safe_percentages)
                self.assertNotRegex(safe_percentages, r"\b\d+(?:[,.]\d+)?%")
                self.assertNotRegex(text.lower(), r"cashback.{0,35}(?:garant|receb|tem|vai)")
                self.assertNotRegex(text.lower(), r"(?:só hoje|ultimas? unidades|últimos? dias|desconto garantido)")
                self.assertLessEqual(text.count("?"), 1, "outgoing bubble contains multiple questions")
                if category == "suporte_handoff":
                    self._assert_support_pitch_absent(text)


if __name__ == "__main__":
    unittest.main()
