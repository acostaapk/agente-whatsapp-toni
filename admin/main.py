import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config" / "agent-config.json"
EXAMPLE_PATH = BASE / "config" / "agent-config.example.json"
STATIC_DIR = BASE / "static"

app = FastAPI(title="Admin — Agente WhatsApp Toní")


def _load() -> dict:
    # Se o config real não existir, cria a partir do exemplo (primeiro run / clone novo).
    if not CONFIG_PATH.exists() and EXAMPLE_PATH.exists():
        _save(json.loads(EXAMPLE_PATH.read_text(encoding="utf-8")))
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _save(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


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


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
