"""Isolated dry-run helpers for the WhatsApp webhook tests."""

import asyncio
import copy
import io
import json
import os
import sqlite3
import sys
import threading
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch


ADMIN_DIR = Path(__file__).resolve().parents[1]
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "test_agent_config.json"
_LOADED_MAIN = None
_BOOTSTRAP_TEMP = None
_BOOTSTRAP_DB = None
TEST_ADMIN_EMAIL = "whatsapp-dry-run@example.invalid"
TEST_ADMIN_PASSWORD = "dry-run-only-password"


class FakeRequest:
    """Minimal FastAPI request double returning a synthetic Cloud API payload."""

    def __init__(self, payload: dict):
        self.payload = payload

    async def json(self) -> dict:
        return self.payload


class WhatsAppDryRun:
    def __init__(self):
        self.replies: list[tuple[str, str]] = []
        self.buttons: list[tuple[str, str, list[tuple[str, str]]]] = []
        self.events: list[tuple[str, str]] = []

    def send_text(self, to: str, text: str) -> None:
        self.replies.append((to, text))
        self.events.append(("text", to))

    def send_buttons(self, to: str, text: str, buttons: list) -> None:
        self.buttons.append((to, text, list(buttons)))
        self.events.append(("buttons", to))


def _load_main():
    """Import the existing route without reading persisted state or starting workers."""
    global _LOADED_MAIN, _BOOTSTRAP_TEMP, _BOOTSTRAP_DB
    if _LOADED_MAIN is not None:
        return _LOADED_MAIN

    if str(ADMIN_DIR) not in sys.path:
        sys.path.insert(0, str(ADMIN_DIR))

    if "admin.main" in sys.modules:
        raise RuntimeError("admin.main was imported before the safe dry-run harness")

    # main.py initializes SQLite, reads config, starts a background loop, and
    # imports code that may open URLs. Isolate all of those at import time.
    import urllib.request

    original_exists = Path.exists
    original_read_text = Path.read_text
    original_connect = sqlite3.connect
    _BOOTSTRAP_TEMP = TemporaryDirectory(prefix="whatsapp-bootstrap-")
    _BOOTSTRAP_DB = Path(_BOOTSTRAP_TEMP.name) / "users.db"
    config_dir = (ADMIN_DIR / "config").resolve()

    def safe_exists(path: Path) -> bool:
        try:
            if path.suffix.lower() == ".json" and path.parent.resolve() == config_dir:
                return False
        except (OSError, RuntimeError):
            pass
        return original_exists(path)

    def safe_read_text(path: Path, *args, **kwargs) -> str:
        try:
            if path.suffix.lower() == ".json" and path.parent.resolve() == config_dir:
                return original_read_text(FIXTURE_PATH, *args, **kwargs)
        except (OSError, RuntimeError):
            pass
        return original_read_text(path, *args, **kwargs)

    def temporary_sqlite_connect(_database, *args, **kwargs):
        return original_connect(_BOOTSTRAP_DB, *args, **kwargs)

    def deny_urlopen(*args, **kwargs):
        raise AssertionError("urllib.request.urlopen is disabled in WhatsApp dry-run tests")

    with ExitStack() as stack:
        stack.enter_context(patch.object(Path, "exists", safe_exists))
        stack.enter_context(patch.object(Path, "read_text", safe_read_text))
        stack.enter_context(patch.object(threading.Thread, "start", lambda self: None))
        stack.enter_context(patch.object(sqlite3, "connect", side_effect=temporary_sqlite_connect))
        stack.enter_context(patch.dict(os.environ, {
            "ADMIN_EMAIL": TEST_ADMIN_EMAIL,
            "ADMIN_PASSWORD": TEST_ADMIN_PASSWORD,
        }))
        stack.enter_context(patch("urllib.request.urlopen", side_effect=deny_urlopen))
        stack.enter_context(patch("sys.stdout", io.StringIO()))
        import importlib

        main = importlib.import_module("admin.main")
    main.USERS_DB_PATH = _BOOTSTRAP_DB
    _LOADED_MAIN = main
    return _LOADED_MAIN


def run_webhook(
    request: FakeRequest,
    dry_run: WhatsAppDryRun,
    *,
    initial_qualify: dict | None = None,
    reset_state: bool = True,
    thread_runner=None,
    http_response: dict | None = None,
):
    """Run the real async webhook with all external and persisted I/O isolated."""
    main = _load_main()
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    config = {key: value for key, value in fixture.items() if not key.startswith("_test_")}
    http_response = http_response or fixture["_test_http_json_response"]

    with TemporaryDirectory(prefix="whatsapp-dry-run-") as temp_dir:
        state_dir = Path(temp_dir)
        state_paths = {
            "HISTORY_PATH": state_dir / "history.json",
            "LEDGER_PATH": state_dir / "messages.jsonl",
            "FOLLOWUP_PATH": state_dir / "followups.json",
            "BLOCKED_PATH": state_dir / "blocked.json",
            "LEAD_PATH": state_dir / "lead_state.json",
            "FUNNEL_PATH": state_dir / "funnel.jsonl",
            "QUALIFY_PATH": state_dir / "qualify.json",
            "USERS_DB_PATH": state_dir / "users.db",
            "DEDUPE_PATH": state_dir / "seen_msgs.json",
        }

        # Isolate in-memory state too: cases must not depend on test order.
        if reset_state:
            main.HISTORY.clear()
            main.FOLLOWUPS.clear()
            main.BLOCKED.clear()
            main.LEADS.clear()
            main.QUALIFY.clear()
            main.SEEN_MSG_IDS.clear()
            main.QUALIFY.update(copy.deepcopy(initial_qualify or {}))

        def fake_http_json(url: str, headers: dict, body: dict) -> dict:
            dry_run.events.append(("http", url))
            return copy.deepcopy(http_response)

        def run_thread_inline(target, *args, **kwargs):
            return target(*args, **kwargs)

        import urllib.request

        def deny_urlopen(*args, **kwargs):
            raise AssertionError("urllib.request.urlopen is disabled in WhatsApp dry-run tests")

        with ExitStack() as stack:
            current_urlopen = urllib.request.urlopen
            if isinstance(current_urlopen, Mock):
                urlopen_mock = current_urlopen
                urlopen_mock.side_effect = deny_urlopen
            else:
                urlopen_mock = stack.enter_context(
                    patch("urllib.request.urlopen", side_effect=deny_urlopen)
                )
            for name, path in state_paths.items():
                stack.enter_context(patch.object(main, name, path))
            stack.enter_context(patch.object(main, "_load", return_value=config))
            stack.enter_context(
                patch.object(
                    main,
                    "_send_reply",
                    side_effect=lambda cfg, to, text: dry_run.send_text(to, text),
                )
            )
            stack.enter_context(
                patch.object(
                    main,
                    "_send_buttons",
                    side_effect=lambda cfg, to, text, buttons: dry_run.send_buttons(
                        to, text, buttons
                    ),
                )
            )
            stack.enter_context(patch.object(main, "_http_json", side_effect=fake_http_json))
            stack.enter_context(
                patch.object(main, "_thread_ctx", side_effect=thread_runner or run_thread_inline)
            )
            stack.enter_context(patch.object(main, "_pausa", return_value=None))
            stack.enter_context(patch.object(main.time, "sleep", return_value=None))
            result = asyncio.run(main.whatsapp_message(request))
            urlopen_mock.assert_not_called()
            return result
