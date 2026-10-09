# Human Support Tools Implementation Plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox syntax.

**Goal:** Add a polished `/mensagens` right rail with an editable shared reply library and manually confirmed machine-image sends visible in the conversation history.

**Architecture:** Keep the existing FastAPI app and vanilla HTML/CSS/JS page. Store the shared reply library as an atomically saved, versioned JSON file in the existing config volume; keep image IDs and URLs server-allowlisted; route sends through current channel helpers and the existing history/ledger.

**Tech Stack:** Python 3.13, FastAPI, existing JSON history/config volume, vanilla HTML/CSS/JS, `unittest`, Docker Compose, `ffmpeg` for one-time JPEG derivatives.

**Spec:** `docs/superpowers/specs/2026-10-08-human-support-tools-design.md`

## Global Constraints

- Any authenticated admin user can edit the shared reply library; do not introduce roles or a new framework.
- Selecting a reply or image only prepares it; sending always needs a separate explicit button click.
- Only the four approved media IDs may be sent; store no image bytes or arbitrary URL in conversation history.
- Image derivatives are RGB/sRGB JPEG files under 5 MB, served from `admin/static/machines/`; never alter the source WebPs.
- Image sends use `_send_media` and honor Meta/Evolution channel selection; blocked recipients are rejected before sending.
- Text/image transport failures do not create sent-history records; ambiguous timeouts are not auto-retried.
- Preserve older `{role, content, ts}` history entries and all existing top-level page tabs.
- Do not inspect runtime configuration, credentials, backups, or customer databases; tests must use temporary state and mocked transports.
- Do not send real WhatsApp messages, stage files, or commit. Production deployment requires separate user approval.
- `test_prompt_consistency.py` reads `admin/config/agent-config.json` if present; exclude it from test commands rather than reading runtime configuration.

---

## Files and responsibilities

| File | Responsibility |
|---|---|
| `admin/main.py` | Reply-library JSON storage/API, allowlisted image metadata, media/text safety checks, and image history metadata. |
| `admin/static/chat.html` | Three-column desktop UI, mobile drawers, reply editor, image preview/caption, explicit sends, transcript rendering. |
| `admin/static/human-support-ui.js` | Dependency-free helpers for preparing reply/image drafts and building an asset-ID send payload without dispatching it. |
| `admin/static/machines/t1.jpg`, `t2.jpg`, `t3.jpg`, `t3-smart.jpg` | Public JPEG derivatives created from existing WebP product images; originals remain untouched. |
| `admin/tests/test_human_support.py` | Isolated API/storage/provider/history regressions using temporary paths and mocked senders. |
| `admin/tests/test_human_support_ui.js` | Node built-in tests for draft preparation and payload shape; no added packages. |
| `admin/.dockerignore` | Excludes runtime config, credentials, databases, backups, tests, docs, and local caches from Docker build context. |
| `docs/superpowers/specs/2026-10-08-human-support-tools-design.md` | Approved requirements and behavioral contract; do not edit implementation scope without user review. |

## Task 1: Shared editable reply library API

**Files:** modify `admin/main.py`; create `admin/tests/test_human_support.py`.

**Interfaces produced:**
- `GET /api/human-support/library` returns `{version: int, replies: list, assets: list}` and requires `_current_user`.
- `PUT /api/human-support/replies` consumes `{base_version: int, replies: [{id, category, label, text}]}` and returns the incremented version plus normalized replies.
- Default replies are the five exact entries in the approved spec; default catalog version is `0` until first write.

- [x] **Step 1: Write tests before production code.** In the new `unittest` file, load `admin.main` through `admin.tests.whatsapp_test_harness._load_main`; patch `_current_user` and `HUMAN_SUPPORT_REPLIES_PATH` to a `TemporaryDirectory`. Include tests for default catalog response, unauthenticated read/write rejection, adding/editing/deleting via full-list replacement, invalid/duplicate/over-40-character IDs, empty text, length/count bounds, atomic-file persistence, and stale `base_version` returning 409 without overwriting the latest data. Snapshot `sys.path` and the harness globals in `setUpClass`, restore them in `tearDownClass`, and add a regression assertion so the test harness does not leak its `ADMIN_DIR` path mutation into later suites. RED observed for missing API, 41-character ID, and path leakage.

Example test setup (all paths remain temporary):

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from admin.tests.whatsapp_test_harness import FakeRequest, _load_main

with TemporaryDirectory() as tmp:
    main = _load_main()
    path = Path(tmp) / "human-support-replies.json"
    with patch.object(main, "HUMAN_SUPPORT_REPLIES_PATH", path), \
         patch.object(main, "_current_user", return_value={"id": 1}):
        result = main.get_human_support_library(FakeRequest({}))
        assert result["version"] == 0
        assert {item["id"] for item in result["replies"]} == {
            "acolhimento", "diagnostico", "foto-print", "pedido", "confirmacao"
        }
```

- [x] **Step 2: Prove RED.** `python3 -m unittest admin.tests.test_human_support -v` failed before implementation on the missing API; the follow-up RED also showed the 41-char ID accepted and `sys.path` leakage.
- [x] **Step 3: Implement the minimal store/API.** Added the seed list, path, and `threading.Lock` in `main.py`; missing storage returns `{version: 0, replies: defaults}`; writes use sibling `.tmp` plus `os.replace`; stale `base_version` returns 409; IDs are unique slugs ≤40 chars; category/label/text are required and bounded to category ≤40, label ≤60, text ≤2,000, at most 100 replies. Reads/writes require `_current_user`; response exposes only reply fields and fixed image metadata. The test class restores its prior `sys.path` and harness globals.
- [x] **Step 4: Prove GREEN.** `python3 -m unittest admin.tests.test_human_support -v` — 7 passed. Combined safe tests for human support, follow-up, sales policy, and funnel: 157 passed. `py_compile` and `git diff --check` passed.

## Task 2: Safe JPEG catalog, channel-correct sends, and media history

**Files:** modify `admin/main.py`; add four JPEGs under `admin/static/machines/`; extend `admin/tests/test_human_support.py`.

**Interfaces consumed:** shared reply API from Task 1.  
**Interfaces produced:** `MEDIA_ASSETS` fixed mapping for `t1`, `t2`, `t3`, `t3_smart`; image send body `{to, asset_id, caption}`; optional history fields `kind="image"`, `asset_id`.

- [x] **Step 1: Write media/history tests.** With temporary history/ledger files, patched auth, and `_send_media` mocked, tests assert allowlisted ID calls the exact fixed HTTPS URL; missing/unknown IDs and blocked recipients make zero calls; success appends assistant history with caption/asset ID and a ledger event; provider failure returns generic 502 and appends nothing; blank caption stores `Imagem enviada: T2`; image history does not contain URL fields; and blocked text is not dispatched or logged.
- [x] **Step 2: Prove RED.** `python3 -m unittest admin.tests.test_human_support -v` failed on the old URL-only endpoint, provider failure branch, image history, and blocked text/media behavior; no external call occurred because tests used mocks.
- [x] **Step 3: Generate only JPEG derivatives.** Converted the four existing WebPs with `ffmpeg`; derivatives are RGB JPEG, 720×1080, 90–115 KB. Source WebPs were not modified.
- [x] **Step 4: Implement allowlisted transport/history.** `/api/whatsapp/send-media` resolves only `asset_id`, rejects caller URLs/blocked recipients, enforces a 1,024-character optional caption, sends through `_send_media(_load(), to, url, caption, mediatype="image")`, and appends history only after success. `_history_append(phone, role, content, metadata=None)` stores only validated `kind`/`asset_id`; ledger content remains caption or marker. Blocked text recipients are rejected before dispatch/history; provider failures return generic errors.
- [x] **Step 5: Prove GREEN.** `python3 -m unittest admin.tests.test_human_support -v` — 13 tests passed. `py_compile` and `git diff --check` passed. UI caller migration remains Task 3; no deployment occurs between these tasks.

## Task 3: Three-column chat and editable right rail

**Files:** modify `admin/static/chat.html`; create `admin/static/human-support-ui.js` and `admin/tests/test_human_support_ui.js`.

**Interfaces consumed:** `GET /api/human-support/library`; `PUT /api/human-support/replies`; existing text route; media route `{to, asset_id, caption}`; conversation history with optional image fields.

- [x] **Step 1: Write failing UI-helper tests.** Added `admin/tests/test_human_support_ui.js` with `node:test` cases for editable reply draft, image preview state, `{to, asset_id, caption}` payload without URL, invalidating an earlier conversation-load token after a newer selection, and clearing text/image drafts only when they still match the submitted draft/version.
- [x] **Step 2: Prove RED.** `node --test admin/tests/test_human_support_ui.js` failed because the helper module did not exist; the Python static-page contract test failed because the right rail was absent.
- [x] **Step 3: Implement the small UMD helper.** `admin/static/human-support-ui.js` exposes pure `prepareReplyDraft`, `prepareImageDraft`, `buildImageSendPayload`, `createRequestGate`, `shouldClearTextDraft`, and `shouldClearImageDraft` on `window.HumanSupportUI` and CommonJS; Node helper tests pass without dependencies or network.
- [x] **Step 4: Establish the page baseline.** Kept all four top-level tabs and their other sections; `#tab-conversas` now uses `286px minmax(0, 1fr) 340px` with a flexible center column and existing Toni palette.
- [x] **Step 5: Add the right rail.** Added `Respostas`/`Maquininhas` tabs, search/category filters, safe text rendering, and shared library CRUD with version-conflict reload. Saving content does not call a message endpoint.
- [x] **Step 6: Add prepare-versus-send behavior.** Reply/image selections only stage drafts; the user must click `Enviar`/`Enviar imagem`. Drafts are keyed by phone; failures preserve them; asset payload contains only the allowlisted ID. Per-phone revisions and in-flight guards ensure an older accepted send cannot clear a newer draft or trigger duplicate sends. Each conversation load shows a loading state, ignores stale request tokens, and replaces a failed current load with an error plus retry button.
- [x] **Step 7: Render image history safely.** Image entries resolve `asset_id` through the safe catalog; legacy text messages remain readable; unknown IDs do not load a URL from history.
- [x] **Step 8: Add mobile drawers/accessibility.** Added labeled drawers, ARIA state, Escape/scrim closing, focus return, semantic live regions, and mobile topbar wrapping so all tabs remain visible at 390px.
- [x] **Step 9: Verify interactions in a local mock-browser run.** Playwright drove the static admin page against synthetic conversations/library and intercepted all API/media/font URLs locally. Desktop grid computed as `286px 814px 340px`; preparing text and image caused zero sends; explicit text/image buttons made one mocked request each; image history rendered; shared library edit/save incremented its version; mobile drawer toggles worked at 390px with no tab/container overflow or page errors. Deferred A-after-B response verified stale thread prevention; aborted history request displayed retry; typing a second text and preparing T2 during delayed T1 sends preserved both later drafts and prevented duplicate sends. Browser screenshots are in `/tmp/opencode`; no live data/network was used.

## Task 4: Cross-cutting verification and release handoff

**Files:** `admin/main.py`, `admin/static/chat.html`, `admin/static/machines/*.jpg`, `admin/tests/test_human_support.py` only as required by fixes.

- [x] **Step 1: Run focused tests.** `python3 -m unittest admin.tests.test_human_support -v` — 15 tests passed, covering library/auth/versioning, image allowlist/channel/history, blocked contacts, UI contract, and send errors.
- [x] **Step 2: Run the safe existing suite without runtime-config reader.** `python3 -m unittest admin.tests.test_human_support admin.tests.test_followup_policy admin.tests.test_sales_policy admin.tests.test_whatsapp_funnel -v` — 165 tests passed. `admin.tests.test_prompt_consistency` was intentionally excluded because it reads `admin/config/agent-config.json` when present.
- [x] **Step 3: Run UI and static checks.** `node --test admin/tests/test_human_support_ui.js` — 6 tests passed; `node --check admin/static/human-support-ui.js` and inline `chat.html` script syntax check passed; `python3 -m py_compile admin/main.py admin/followup_policy.py admin/sales_policy.py` and `git diff --check` exited 0.
- [x] **Step 4: Build a local verification image only.** Added `admin/.dockerignore` entries for `.env*`, `config/`, `*.db`, `*.jsonl`, `*.bak*`, `backups/`, `tests/`, `docs/`, `__pycache__/`, and `*.py[cod]`; kept the Dockerfile-required source, `static/`, and `prompts/`. `docker build -t infra-admin:human-support-check -f admin/Dockerfile admin` succeeded (build context 965 B). A `--network none` entrypoint verified `/app/config` is absent and SHA-256 matches for `main.py`, `chat.html`, the JS helper, and all four JPEGs. The running service image was not replaced.
- [x] **Step 5: Deploy after separate approval.** User approved deployment. Retagged `infra-admin:human-support-check` as `infra-admin:latest` and ran `docker compose up -d --no-deps --force-recreate admin`; only `agente-admin` was recreated. `/mensagens` returns unauthenticated 302 to `/login`, `/login` returns 200, and all four public JPEG URLs return HTTP 200 `image/jpeg`. Container/workspace `main.py` SHA-256 matches. Evolution API/DB remained up with unchanged IDs/start times. No real WhatsApp message was sent.

## Self-review

- Spec sections 2–4 map to Tasks 1–3 (shared editable replies, seeded copy, JPEG allowlist).
- Spec sections 5–6 map to Task 3 (layout, responsive drawers, drafts, accessibility) and Tasks 1–2 (auth, versioned persistence, media, block checks, history, error handling).
- Spec sections 7–8 map to all tasks and the local verification in Task 4.
- Runtime config is excluded from tests; `test_prompt_consistency.py` is intentionally omitted.
- At plan creation, no source code had been changed for this feature; implementation changes are tracked in the task ledger and have not been staged or committed.
