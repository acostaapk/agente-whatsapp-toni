import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from admin.tests.whatsapp_test_harness import FakeRequest, _load_main


DEFAULT_REPLIES = [
    {
        "id": "acolhimento",
        "category": "Atendimento",
        "label": "Acolhimento",
        "text": "Entendi. Vou verificar isso e te retorno por aqui.",
    },
    {
        "id": "diagnostico",
        "category": "Atendimento",
        "label": "Diagnóstico",
        "text": "Qual modelo você está usando e o que aparece na tela?",
    },
    {
        "id": "foto-print",
        "category": "Atendimento",
        "label": "Foto ou print",
        "text": "Se puder, envie uma foto ou um print do que aparece.",
    },
    {
        "id": "pedido",
        "category": "Pedido",
        "label": "Etapa do pedido",
        "text": "Me diga em qual etapa do pedido você está e qual mensagem aparece.",
    },
    {
        "id": "confirmacao",
        "category": "Atendimento",
        "label": "Confirmar resolução",
        "text": "Conseguiu resolver? Se não, me conte em qual etapa precisa de ajuda.",
    },
]


class HumanSupportLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._previous_sys_path = list(sys.path)
        from admin.tests import whatsapp_test_harness

        cls._harness = whatsapp_test_harness
        cls._previous_harness_state = (
            whatsapp_test_harness._LOADED_MAIN,
            whatsapp_test_harness._BOOTSTRAP_TEMP,
            whatsapp_test_harness._BOOTSTRAP_DB,
        )
        cls._previous_main_module = sys.modules.get("admin.main")

    @classmethod
    def tearDownClass(cls):
        loaded_main, bootstrap_temp, bootstrap_db = cls._previous_harness_state
        current_temp = cls._harness._BOOTSTRAP_TEMP
        if current_temp is not None and current_temp is not bootstrap_temp:
            current_temp.cleanup()
        cls._harness._LOADED_MAIN = loaded_main
        cls._harness._BOOTSTRAP_TEMP = bootstrap_temp
        cls._harness._BOOTSTRAP_DB = bootstrap_db
        if cls._previous_main_module is None:
            sys.modules.pop("admin.main", None)
        else:
            sys.modules["admin.main"] = cls._previous_main_module
        sys.path[:] = cls._previous_sys_path
        if sys.path != cls._previous_sys_path:
            raise AssertionError("human-support tests leaked changes to sys.path")

    def setUp(self):
        self.main = _load_main()
        self.temp_dir = TemporaryDirectory(prefix="human-support-library-")
        self.addCleanup(self.temp_dir.cleanup)
        self.replies_path = Path(self.temp_dir.name) / "human-support-replies.json"
        self.history_path = Path(self.temp_dir.name) / "history.json"
        self.ledger_path = Path(self.temp_dir.name) / "messages.jsonl"
        self.path_patch = patch.object(
            self.main, "HUMAN_SUPPORT_REPLIES_PATH", self.replies_path, create=True
        )
        self.history_patch = patch.object(self.main, "HISTORY_PATH", self.history_path)
        self.ledger_patch = patch.object(self.main, "LEDGER_PATH", self.ledger_path)
        self.user_patch = patch.object(
            self.main, "_current_user", return_value={"id": 1}
        )
        self.path_patch.start()
        self.history_patch.start()
        self.ledger_patch.start()
        self.user_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.addCleanup(self.history_patch.stop)
        self.addCleanup(self.ledger_patch.stop)
        self.addCleanup(self.user_patch.stop)

    def _send_media(self, to, asset_id, caption=""):
        return asyncio.run(
            self.main.wa_send_media(
                FakeRequest({"to": to, "asset_id": asset_id, "caption": caption})
            )
        )

    @staticmethod
    def _status_code(response):
        return getattr(response, "status_code", 200)

    def _get_library(self):
        return self.main.get_human_support_library(FakeRequest({}))

    def _put_replies(self, base_version, replies):
        request = FakeRequest({"base_version": base_version, "replies": replies})
        return asyncio.run(self.main.put_human_support_replies(request))

    @staticmethod
    def _json_response(response):
        if isinstance(response, dict):
            return response
        return json.loads(response.body.decode("utf-8"))

    def test_get_returns_version_zero_seed_and_fixed_public_assets(self):
        result = self._get_library()

        self.assertEqual(result["version"], 0)
        self.assertEqual(result["replies"], DEFAULT_REPLIES)
        self.assertEqual(
            result["assets"],
            [
                {
                    "id": "t1",
                    "name": "T1",
                    "alt": "Maquininha Ton T1",
                    "preview_url": "https://toni.analisereview.com.br/static/machines/t1.jpg",
                },
                {
                    "id": "t2",
                    "name": "T2",
                    "alt": "Maquininha Ton T2",
                    "preview_url": "https://toni.analisereview.com.br/static/machines/t2.jpg",
                },
                {
                    "id": "t3",
                    "name": "T3",
                    "alt": "Maquininha Ton T3",
                    "preview_url": "https://toni.analisereview.com.br/static/machines/t3.jpg",
                },
                {
                    "id": "t3_smart",
                    "name": "T3 Smart",
                    "alt": "Maquininha Ton T3 Smart",
                    "preview_url": "https://toni.analisereview.com.br/static/machines/t3-smart.jpg",
                },
            ],
        )
        self.assertFalse(self.replies_path.exists())

    def test_get_and_put_reject_unauthenticated_requests(self):
        with patch.object(self.main, "_current_user", return_value=None):
            get_response = self._get_library()
            put_response = self._put_replies(0, [])

        self.assertEqual(get_response.status_code, 401)
        self.assertEqual(put_response.status_code, 401)
        self.assertFalse(self.replies_path.exists())

    def test_put_replaces_shared_list_and_increments_version(self):
        first = [{"id": "pedido-status", "category": "Pedido", "label": "Status", "text": "  Qual é o status?  "}]
        response1 = self._put_replies(0, first)
        payload1 = self._json_response(response1)

        self.assertEqual(payload1["version"], 1)
        self.assertEqual(
            payload1["replies"],
            [{"id": "pedido-status", "category": "Pedido", "label": "Status", "text": "Qual é o status?"}],
        )

        replacement = [{"id": "pedido-status", "category": "Pedido", "label": "Andamento", "text": "Em qual etapa está?"}]
        response2 = self._put_replies(1, replacement)
        payload2 = self._json_response(response2)
        reloaded = self._get_library()

        self.assertEqual(payload2["version"], 2)
        self.assertEqual(payload2["replies"], replacement)
        self.assertEqual(reloaded["version"], 2)
        self.assertEqual(reloaded["replies"], replacement)

    def test_put_uses_atomic_replace_and_stale_version_preserves_latest_data(self):
        first = [{"id": "resposta-a", "category": "Atendimento", "label": "A", "text": "Texto A"}]
        with patch.object(self.main.os, "replace", wraps=os.replace) as atomic_replace:
            response = self._put_replies(0, first)

        self.assertEqual(self._json_response(response)["version"], 1)
        atomic_replace.assert_called_once()
        temporary_path, target_path = atomic_replace.call_args.args
        self.assertEqual(Path(target_path), self.replies_path)
        self.assertNotEqual(Path(temporary_path), self.replies_path)
        self.assertFalse(Path(temporary_path).exists())

        stale = [{"id": "resposta-b", "category": "Pedido", "label": "B", "text": "Texto B"}]
        conflict = self._put_replies(0, stale)

        self.assertEqual(conflict.status_code, 409)
        stored = json.loads(self.replies_path.read_text(encoding="utf-8"))
        self.assertEqual(stored, {"version": 1, "replies": first})

    def test_put_rejects_invalid_or_duplicate_stable_ids_and_empty_fields(self):
        invalid_lists = [
            [{"id": "not a stable id", "category": "Atendimento", "label": "Ajuda", "text": "Texto"}],
            [
                {"id": "same-id", "category": "Atendimento", "label": "Um", "text": "Texto 1"},
                {"id": "same-id", "category": "Pedido", "label": "Dois", "text": "Texto 2"},
            ],
            [{"id": "vazio", "category": " ", "label": "Ajuda", "text": "Texto"}],
            [{"id": "vazio", "category": "Atendimento", "label": " ", "text": "Texto"}],
            [{"id": "vazio", "category": "Atendimento", "label": "Ajuda", "text": "  "}],
            [{"id": "faltando", "category": "Atendimento", "label": "Ajuda"}],
        ]

        for replies in invalid_lists:
            with self.subTest(replies=replies):
                response = self._put_replies(0, replies)
                self.assertEqual(response.status_code, 400)

        self.assertFalse(self.replies_path.exists())

    def test_put_rejects_reply_id_longer_than_40_characters(self):
        reply = {
            "id": "a" * 41,
            "category": "Atendimento",
            "label": "Ajuda",
            "text": "Texto",
        }

        response = self._put_replies(0, [reply])

        self.assertEqual(getattr(response, "status_code", 200), 400)

    def test_put_enforces_reply_count_and_field_length_limits(self):
        valid = {"id": "ok", "category": "c" * 40, "label": "l" * 60, "text": "t" * 2000}
        self.assertEqual(self._json_response(self._put_replies(0, [valid]))["version"], 1)

        too_long = [
            {"id": "category-long", "category": "c" * 41, "label": "Label", "text": "Texto"},
            {"id": "label-long", "category": "Categoria", "label": "l" * 61, "text": "Texto"},
            {"id": "text-long", "category": "Categoria", "label": "Label", "text": "t" * 2001},
        ]
        for reply in too_long:
            with self.subTest(reply_id=reply["id"]):
                response = self._put_replies(1, [reply])
                self.assertEqual(response.status_code, 400)

        too_many = [
            {"id": f"reply-{index}", "category": "Atendimento", "label": "Ajuda", "text": "Texto"}
            for index in range(101)
        ]
        self.assertEqual(self._put_replies(1, too_many).status_code, 400)

    def test_media_send_uses_allowlisted_asset_and_records_success_for_each_channel(self):
        for channel in ("meta", "baileys"):
            with self.subTest(channel=channel):
                phone = f"1555555000{0 if channel == 'meta' else 1}"
                config = {"canal": channel}
                with (
                    patch.object(self.main, "_load", return_value=config),
                    patch.object(self.main, "_is_blocked", return_value=False),
                    patch.object(self.main, "_send_media") as send_media,
                ):
                    result = self._send_media(phone, "t2", "Imagem da T2")

                self.assertEqual(self._json_response(result), {"ok": True})
                send_media.assert_called_once_with(
                    config,
                    phone,
                    "https://toni.analisereview.com.br/static/machines/t2.jpg",
                    "Imagem da T2",
                    mediatype="image",
                )
                history = self.main._history_get(phone)
                self.assertEqual(len(history), 1)
                self.assertEqual(history[0]["role"], "assistant")
                self.assertEqual(history[0]["content"], "Imagem da T2")
                self.assertEqual(history[0]["kind"], "image")
                self.assertEqual(history[0]["asset_id"], "t2")
                self.assertNotIn("url", history[0])
                self.assertNotIn("preview_url", history[0])

        ledger_rows = (
            [json.loads(line) for line in self.ledger_path.read_text(encoding="utf-8").splitlines()]
            if self.ledger_path.exists()
            else []
        )
        self.assertEqual(len(ledger_rows), 2)
        self.assertEqual([row["content"] for row in ledger_rows], ["Imagem da T2"] * 2)

    def test_media_send_rejects_unknown_asset_without_using_supplied_url(self):
        phone = "15555550002"
        request = FakeRequest({
            "to": phone,
            "asset_id": "unknown",
            "url": "https://malicious.invalid/image.png",
            "caption": "Imagem",
        })
        with (
            patch.object(self.main, "_is_blocked", return_value=False),
            patch.object(self.main, "_load", return_value={"canal": "meta"}),
            patch.object(self.main, "_send_media") as send_media,
            patch.object(self.main, "_wa_api") as direct_graph_call,
        ):
            response = asyncio.run(self.main.wa_send_media(request))

        self.assertEqual(self._status_code(response), 400)
        send_media.assert_not_called()
        direct_graph_call.assert_not_called()
        self.assertEqual(self.main._history_get(phone), [])

    def test_media_send_rejects_blocked_contact_without_dispatch_or_history(self):
        phone = "15555550003"
        with (
            patch.object(self.main, "_is_blocked", return_value=True),
            patch.object(self.main, "_load", return_value={"canal": "meta"}),
            patch.object(self.main, "_send_media") as send_media,
            patch.object(self.main, "_wa_api") as direct_graph_call,
        ):
            response = self._send_media(phone, "t2", "Imagem da T2")

        self.assertEqual(self._status_code(response), 403)
        send_media.assert_not_called()
        direct_graph_call.assert_not_called()
        self.assertEqual(self.main._history_get(phone), [])

    def test_media_send_failure_returns_generic_error_and_does_not_record_history(self):
        phone = "15555550004"
        with (
            patch.object(self.main, "_is_blocked", return_value=False),
            patch.object(self.main, "_load", return_value={"canal": "meta"}),
            patch.object(self.main, "_send_media", side_effect=RuntimeError("PRIVATE-DETAIL")),
            patch.object(self.main, "_wa_api") as direct_graph_call,
        ):
            response = self._send_media(phone, "t2", "Imagem da T2")

        self.assertEqual(self._status_code(response), 502)
        self.assertNotIn("PRIVATE-DETAIL", response.body.decode("utf-8"))
        direct_graph_call.assert_not_called()
        self.assertEqual(self.main._history_get(phone), [])
        self.assertFalse(self.ledger_path.exists())

    def test_blank_image_caption_stores_safe_history_marker(self):
        phone = "15555550005"
        with (
            patch.object(self.main, "_is_blocked", return_value=False),
            patch.object(self.main, "_load", return_value={"canal": "baileys"}),
            patch.object(self.main, "_send_media") as send_media,
        ):
            response = self._send_media(phone, "t2", "")

        self.assertEqual(self._json_response(response), {"ok": True})
        send_media.assert_called_once()
        self.assertEqual(
            self.main._history_get(phone)[0]["content"], "Imagem enviada: T2"
        )

    def test_blocked_text_send_is_rejected_without_dispatch_or_history(self):
        phone = "15555550006"
        with (
            patch.object(self.main, "_is_blocked", return_value=True),
            patch.object(self.main, "_send_reply") as send_reply,
        ):
            response = asyncio.run(
                self.main.send_conversation_message(
                    phone, FakeRequest({"text": "Resposta de suporte"})
                )
            )

        self.assertEqual(self._status_code(response), 403)
        send_reply.assert_not_called()
        self.assertEqual(self.main._history_get(phone), [])

    def test_text_send_failure_hides_provider_details_and_does_not_record_history(self):
        phone = "15555550007"
        with patch.object(
            self.main, "_send_reply", side_effect=RuntimeError("PRIVATE-PROVIDER-DETAIL")
        ):
            response = asyncio.run(
                self.main.send_conversation_message(
                    phone, FakeRequest({"text": "Resposta de suporte"})
                )
            )

        self.assertEqual(self._status_code(response), 502)
        self.assertNotIn("PRIVATE-PROVIDER-DETAIL", self._json_response(response).values())
        self.assertEqual(self.main._history_get(phone), [])
        self.assertFalse(self.ledger_path.exists())

    def test_chat_page_exposes_support_rail_and_asset_id_controls(self):
        page = (self.main.STATIC_DIR / "chat.html").read_text(encoding="utf-8")

        for element_id in (
            "support-panel",
            "reply-search",
            "reply-list",
            "machine-list",
            "reply-editor-form",
            "image-preview",
            "image-caption",
            "send-image",
            "tools-drawer-toggle",
            "conversations-drawer-toggle",
            "sound-toggle",
            "bot-toggle",
        ):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', page)
        self.assertNotIn('id="img-url"', page)
        self.assertIn('aria-live="polite"', page)
        self.assertIn('history-load-error', page)
        self.assertIn('history-retry', page)
        self.assertIn('conversationRequestGate', page)
        self.assertIn('formatSupportTimestamp', page)
        self.assertIn('shouldPlayMessageSound', page)
        self.assertIn('data-tab="templates"', page)
        self.assertIn('data-tab="perfil"', page)
        self.assertIn('data-tab="numeros"', page)


class BotToggleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._previous_sys_path = list(sys.path)
        from admin.tests import whatsapp_test_harness

        cls._harness = whatsapp_test_harness
        cls._previous_harness_state = (
            whatsapp_test_harness._LOADED_MAIN,
            whatsapp_test_harness._BOOTSTRAP_TEMP,
            whatsapp_test_harness._BOOTSTRAP_DB,
        )
        cls._previous_main_module = sys.modules.get("admin.main")

    @classmethod
    def tearDownClass(cls):
        loaded_main, bootstrap_temp, bootstrap_db = cls._previous_harness_state
        current_temp = cls._harness._BOOTSTRAP_TEMP
        if current_temp is not None and current_temp is not bootstrap_temp:
            current_temp.cleanup()
        cls._harness._LOADED_MAIN = loaded_main
        cls._harness._BOOTSTRAP_TEMP = bootstrap_temp
        cls._harness._BOOTSTRAP_DB = bootstrap_db
        if cls._previous_main_module is None:
            sys.modules.pop("admin.main", None)
        else:
            sys.modules["admin.main"] = cls._previous_main_module
        sys.path[:] = cls._previous_sys_path

    def setUp(self):
        self.main = _load_main()
        self.temp_dir = TemporaryDirectory(prefix="bot-toggle-")
        self.addCleanup(self.temp_dir.cleanup)
        self.state_path = Path(self.temp_dir.name) / "bot-state.json"
        self.history_path = Path(self.temp_dir.name) / "history.json"
        self.ledger_path = Path(self.temp_dir.name) / "messages.jsonl"
        for name, path in (
            ("BOT_STATE_PATH", self.state_path),
            ("HISTORY_PATH", self.history_path),
            ("LEDGER_PATH", self.ledger_path),
        ):
            patcher = patch.object(self.main, name, path, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        user_patcher = patch.object(self.main, "_current_user", return_value={"id": 1})
        user_patcher.start()
        self.addCleanup(user_patcher.stop)

    @staticmethod
    def _status_code(response):
        return getattr(response, "status_code", 200)

    @staticmethod
    def _json_response(response):
        if isinstance(response, dict):
            return response
        return json.loads(response.body.decode("utf-8"))

    def _get_status(self):
        return self.main.get_bot_status(FakeRequest({}))

    def _set_status(self, enabled):
        return asyncio.run(self.main.set_bot_status(FakeRequest({"enabled": enabled})))

    def test_status_defaults_to_enabled(self):
        self.assertEqual(self._json_response(self._get_status()), {"enabled": True})

    def test_put_disables_and_persists(self):
        self.assertEqual(self._json_response(self._set_status(False)), {"enabled": False})
        self.assertEqual(json.loads(self.state_path.read_text(encoding="utf-8")), {"enabled": False})
        self.assertEqual(self._json_response(self._get_status()), {"enabled": False})
        self.assertEqual(self._json_response(self._set_status(True)), {"enabled": True})

    def test_put_rejects_non_boolean(self):
        response = asyncio.run(self.main.set_bot_status(FakeRequest({"enabled": "off"})))
        self.assertEqual(self._status_code(response), 400)

    def test_status_endpoints_reject_unauthenticated_requests(self):
        with patch.object(self.main, "_current_user", return_value=None):
            self.assertEqual(
                self._status_code(self.main.get_bot_status(FakeRequest({}))), 401)
            self.assertEqual(
                self._status_code(asyncio.run(self.main.set_bot_status(FakeRequest({"enabled": False})))), 401)

    def _meta_text(self, sender, text, mid):
        return {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"messages": [{
            "from": sender, "id": f"wamid.{mid}", "type": "text", "text": {"body": text}}]}}]}]}

    def _meta_button(self, sender, button_id):
        return {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"messages": [{
            "from": sender, "id": f"wamid.{button_id}", "type": "interactive",
            "interactive": {"button_reply": {"id": button_id}}}]}}]}]}

    def test_disabled_text_is_recorded_but_silenced(self):
        from admin.tests.whatsapp_test_harness import WhatsAppDryRun, run_webhook

        self.state_path.write_text(json.dumps({"enabled": False}), encoding="utf-8")
        dry_run = WhatsAppDryRun()
        result = run_webhook(
            FakeRequest(self._meta_text("15555551101", "Oi", "off-1")), dry_run)
        self.assertEqual(result["status"], "bot_pausado")
        self.assertEqual(dry_run.replies, [])
        self.assertEqual(dry_run.buttons, [])
        history = self.main._history_get("15555551101")
        self.assertEqual([m["content"] for m in history if m["role"] == "user"], ["Oi"])

    def test_disabled_button_is_silenced(self):
        from admin.tests.whatsapp_test_harness import WhatsAppDryRun, run_webhook

        self.state_path.write_text(json.dumps({"enabled": False}), encoding="utf-8")
        dry_run = WhatsAppDryRun()
        result = run_webhook(
            FakeRequest(self._meta_button("15555551102", "loja_fisica")), dry_run)
        self.assertEqual(result["status"], "bot_pausado")
        self.assertEqual(dry_run.replies, [])
        self.assertEqual(dry_run.buttons, [])

    def test_enabled_greeting_flows_normally(self):
        from admin.tests.whatsapp_test_harness import WhatsAppDryRun, run_webhook

        dry_run = WhatsAppDryRun()
        result = run_webhook(
            FakeRequest(self._meta_text("15555551103", "Oi", "on-1")), dry_run)
        self.assertEqual(result["status"], "greeted_tipo")

    def test_panel_send_works_while_bot_disabled(self):
        self.state_path.write_text(json.dumps({"enabled": False}), encoding="utf-8")
        with (
            patch.object(self.main, "_is_blocked", return_value=False),
            patch.object(self.main, "_send_reply", return_value=None),
        ):
            response = asyncio.run(
                self.main.send_conversation_message(
                    "15555551104", FakeRequest({"text": "Olá, sou o André"}))
            )
        self.assertEqual(response, {"ok": True})

    def test_followup_suppressed_while_bot_disabled(self):
        import time

        phone = "15555551105"
        offer_ts = time.time() - 31 * 60
        self.main.HISTORY[phone] = [
            {"role": "user", "content": "Oi", "ts": offer_ts - 10},
            {"role": "assistant",
             "content": "Link: https://checkout.example.invalid/mega-plus/t3",
             "ts": offer_ts},
        ]
        self.main.QUALIFY[phone] = {"step": "recomendacao"}
        self.addCleanup(self.main.HISTORY.pop, phone)
        self.addCleanup(self.main.QUALIFY.pop, phone)
        with patch.object(
            self.main, "_configured_checkout_links",
            return_value={"https://checkout.example.invalid/mega-plus/t3"},
        ):
            self.assertTrue(self.main._needs_purchase_followup(phone))
            self.state_path.write_text(json.dumps({"enabled": False}), encoding="utf-8")
            self.assertFalse(self.main._needs_purchase_followup(phone))

    def test_chat_page_exposes_bot_toggle(self):
        page = (self.main.STATIC_DIR / "chat.html").read_text(encoding="utf-8")
        self.assertIn('id="bot-toggle"', page)
        self.assertIn("/api/bot/status", page)


if __name__ == "__main__":
    unittest.main()
