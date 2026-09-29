import json
import urllib.request
import urllib.error
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config" / "agent-config.json"
EXAMPLE_PATH = BASE / "config" / "agent-config.example.json"
STATIC_DIR = BASE / "static"

app = FastAPI(title="Admin — Agente WhatsApp Toní")

VERIFY_TOKEN = "toni-verify-2026"


def _load() -> dict:
    if not CONFIG_PATH.exists() and EXAMPLE_PATH.exists():
        _save(json.loads(EXAMPLE_PATH.read_text(encoding="utf-8")))
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _save(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _http_json(url: str, headers: dict, body: dict) -> dict:
    req = urllib.request.Request(
        url,
        method="POST",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _build_system(cfg: dict) -> str:
    repl = {
        "{{agente_nome}}": cfg.get("agente_nome") or "",
        "{{empresa_nome}}": cfg.get("empresa_nome") or "",
        "{{servico_principal}}": cfg.get("servico_principal") or "",
        "{{criterio_alto_valor}}": cfg.get("criterio_alto_valor") or "",
        "{{link_app}}": cfg.get("link_app") or "",
        "{{base_conhecimento}}": cfg.get("base_conhecimento") or "",
    }
    sm = cfg.get("system_message") or ""
    for k, v in repl.items():
        sm = sm.replace(k, v)
    sm = sm.replace("{{modelos}}", json.dumps(cfg.get("modelos") or [], ensure_ascii=False))
    return sm


# ---- Config (interface admin) ----
@app.get("/api/config")
def get_config():
    return _load()


@app.put("/api/config")
async def put_config(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "JSON inválido"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "corpo deve ser um objeto"}, status_code=400)
    if body.get("canal") not in ("baileys", "meta"):
        return JSONResponse({"error": "canal deve ser 'baileys' ou 'meta'"}, status_code=400)
    _save(body)
    return {"ok": True, "canal": body.get("canal")}


# ---- Webhook do WhatsApp (Meta) ----
@app.get("/webhook/whatsapp")
def whatsapp_verify(request: Request):
    q = request.query_params
    mode = q.get("hub.mode") or ""
    token = q.get("hub.verify_token") or ""
    challenge = q.get("hub.challenge") or ""
    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        return Response(content=challenge, media_type="text/plain", status_code=200)
    return Response(content="forbidden", status_code=403)


@app.post("/webhook/whatsapp")
async def whatsapp_message(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    entry = (body.get("entry") or [{}])[0] or {}
    change = (entry.get("changes") or [{}])[0] or {}
    value = change.get("value") or {}
    msgs = value.get("messages") or []
    if not msgs:
        return {"ok": True, "status": "no_message"}

    msg = msgs[0]
    from_ = msg.get("from") or ""
    texto = (msg.get("text") or {}).get("body", "") if msg.get("type") == "text" else ""
    if not from_ or not texto:
        return {"ok": True, "status": "no_text"}

    cfg = _load()
    system = _build_system(cfg)
    llm = cfg.get("llm") or {}
    meta = cfg.get("meta") or {}

    base = llm.get("base_url") or "http://127.0.0.1:8888/v1"
    model = llm.get("model") or "unsloth/gemma-4-12B-it-qat-GGUF"
    llm_key = llm.get("api_key") or ""

    try:
        llm_resp = _http_json(
            base + "/chat/completions",
            {"Authorization": "Bearer " + llm_key},
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": texto},
                ],
                "max_tokens": 512,
            },
        )
    except Exception as e:
        return JSONResponse({"ok": False, "erro": "llm: " + str(e)}, status_code=502)

    choice = (llm_resp.get("choices") or [{}])[0] or {}
    resposta = (choice.get("message") or {}).get("content", "").strip()
    if not resposta:
        resposta = (choice.get("message") or {}).get("reasoning_content", "").strip()
    if not resposta:
        return {"ok": True, "status": "llm_empty"}

    version = meta.get("version") or "v25.0"
    phone = meta.get("phone_number_id") or ""
    token = meta.get("access_token") or ""

    try:
        _http_json(
            "https://graph.facebook.com/" + version + "/" + phone + "/messages",
            {"Authorization": "Bearer " + token},
            {"messaging_product": "whatsapp", "to": from_, "type": "text", "text": {"body": resposta}},
        )
        return {"ok": True, "status": "sent", "resposta": resposta}
    except Exception as e:
        return JSONResponse({"ok": False, "erro": "whatsapp: " + str(e)}, status_code=502)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
