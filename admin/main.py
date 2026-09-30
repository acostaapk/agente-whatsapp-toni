import json
import base64
import os
import re
import time
import threading
import unicodedata
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
QR_PATH = Path("/home/andre/marketing/agente-whatsapp/infra/qr-toni.png")

app = FastAPI(title="Admin — Agente WhatsApp Toní")

VERIFY_TOKEN = "toni-verify-2026"

# Conversa por número (persistida em disco; uvicorn roda com 1 worker)
HISTORY_PATH = BASE / "config" / "history.json"
HISTORY = {}
HISTORY_LOCK = threading.Lock()
MAX_HISTORY = 20
HISTORY_TTL = 3600  # 1h
MAX_PHONES = 500


def _history_load() -> None:
    try:
        if HISTORY_PATH.exists():
            data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                now = time.time()
                with HISTORY_LOCK:
                    for phone, msgs in data.items():
                        if isinstance(msgs, list):
                            kept = [m for m in msgs if now - m.get("ts", 0) < HISTORY_TTL][-MAX_HISTORY:]
                            if kept:
                                HISTORY[phone] = kept
    except Exception as e:
        print(f"[history] load falhou: {e}", flush=True)


def _history_save() -> None:
    try:
        tmp = HISTORY_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(HISTORY, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, HISTORY_PATH)
    except Exception as e:
        print(f"[history] save falhou: {e}", flush=True)


def _history_get(phone: str) -> list:
    now = time.time()
    with HISTORY_LOCK:
        return [m for m in HISTORY.get(phone, []) if now - m.get("ts", 0) < HISTORY_TTL]


def _history_append(phone: str, role: str, content: str) -> None:
    with HISTORY_LOCK:
        hist = HISTORY.setdefault(phone, [])
        hist.append({"role": role, "content": content, "ts": time.time()})
        del hist[:-MAX_HISTORY]
        if len(HISTORY) > MAX_PHONES:
            oldest = min(HISTORY, key=lambda p: HISTORY[p][-1].get("ts", 0) if HISTORY[p] else 0)
            del HISTORY[oldest]
        _history_save()


def _history_clear(phone: str) -> None:
    with HISTORY_LOCK:
        HISTORY.pop(phone, None)
        _history_save()


def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


FORGET_PREFIXES = ("esquec", "limpar conversa", "limpar o historico", "recomecar", "resetar conversa", "apagar conversa", "apagar o historico")


def _is_forget(texto: str) -> bool:
    n = _norm(texto)
    if n.startswith(FORGET_PREFIXES):
        return True
    m = re.search(r"\b(esque\w*|recome\w*|reset\w*|limpar conversa|apagar conversa)\b", n)
    if not m:
        return False
    before = n[:m.start()]
    if re.search(r"\b(nao|nem|nunca|jamais)\b", before[-25:]):
        return False
    return True


_history_load()


def _strip_markdown(texto: str) -> str:
    texto = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", texto)  # remove imagens markdown (![]())
    texto = re.sub(r"\[([^\]]*)\]\((https?://[^)]+)\)", r"\1 \2", texto)  # [texto](url) -> texto url
    texto = re.sub(r"\*\*(.+?)\*\*", r"\1", texto)  # **negrito** -> texto (WhatsApp usa *simples*)
    texto = re.sub(r"__(.+?)__", r"\1", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


NEGOCIO_KEYWORDS = [
    "padaria", "restaurante", "bar", "lanchonete", "loja", "roupas", "confecção",
    "salão", "barbearia", "farmácia", "drogaria", "mercado", "mercadinho",
    "açougue", "pet shop", "oficina", "cafeteria", "pizzaria", "sorveteria",
    "papelaria", "food truck", "ambulante", "feira", "clínica", "consultório",
    "academia", "hotel", "pousada", "supermercado", "lavanderia", "autopeças",
]


def _extract_slots(user_texts: list) -> dict:
    """Extrai dados já informados (determinístico — não depende do humor do LLM)."""
    slots: dict = {}
    for t in user_texts:
        low = (t or "").lower()
        if "negocio" not in slots:
            m = re.search(r"tenho\s+(?:um|uma)\s+([a-zà-ú]+(?:\s+[a-zà-ú]+){0,3})", low)
            if m:
                val = re.split(r"\s+e(?:\s+|$)", m.group(1).strip())[0].strip()
                slots["negocio"] = val
            else:
                for kw in NEGOCIO_KEYWORDS:
                    if kw in low:
                        slots["negocio"] = kw
                        break
        if "volume" not in slots:
            m = (re.search(r"(\d[\d\.,]*\s*(?:mil|\bk\b)?)\s*(?:por\s*m[eê]s|/m[eê]s|mensais?)", low)
                 or re.search(r"(?:vendo|vendas?|fatur|faturamento)[^\d]*(\d[\d\.,]*\s*(?:mil|\bk\b)?)", low))
            if m:
                slots["volume"] = m.group(1).strip()
        if "comprovante" not in slots:
            if "impress" in low:
                slots["comprovante"] = "impresso"
            elif "digital" in low:
                slots["comprovante"] = "digital"
        if "mobilidade" not in slots:
            if re.search(r"celular por perto|com o celular", low):
                slots["mobilidade"] = "celular por perto"
            elif re.search(r"sozinha|sem (o )?celular|sem depender|mobilidade|independente", low):
                slots["mobilidade"] = "funciona sozinha"
        if "cadastro" not in slots:
            if re.search(r"\bcnpj\b", low):
                slots["cadastro"] = "CNPJ"
            elif re.search(r"\bmei\b", low):
                slots["cadastro"] = "MEI"
            elif re.search(r"\bcpf\b", low):
                slots["cadastro"] = "CPF"
    return slots


def _slots_note(slots: dict) -> str:
    if not slots:
        return ""
    nomes = {"negocio": "Tipo de negócio", "volume": "Vendas mensais",
             "comprovante": "Comprovante", "mobilidade": "Mobilidade", "cadastro": "Cadastro"}
    linhas = "\n".join(f"- {nomes[k]}: {v}" for k, v in slots.items() if k in nomes)
    return ("\n\n# DADOS JÁ INFORMADOS PELO CLIENTE (extraídos automaticamente)\n"
            "Estas informações já foram dadas. NÃO pergunte nenhuma delas novamente; "
            "confirme em meia frase e siga para a próxima informação pendente.\n" + linhas)


def _apply_model_link(texto: str, modelo: dict) -> str:
    """Garante o link curto e CORRETO do modelo: troca qualquer checkout longo
    (ou link curto de outro modelo) pelo link oficial do modelo detectado."""
    short = ((modelo or {}).get("link") or "").strip()
    if not short:
        return texto
    texto = re.sub(r"https?://(?:www\.)?ton\.com\.br/checkout[^\s]*", short, texto)
    texto = re.sub(r"https?://analisereview\.com\.br/ton/[^\s]*", short, texto)
    if short not in texto:
        texto = texto.rstrip() + "\n\n" + short
    return texto.strip()


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


def _extract_message(body) -> tuple:
    """Extrai (from, texto) de uma mensagem recebida. Retorna (None, None) se não for mensagem de texto."""
    # Meta Cloud API
    if body.get("object") == "whatsapp_business_account":
        entry = (body.get("entry") or [{}])[0] or {}
        change = (entry.get("changes") or [{}])[0] or {}
        value = change.get("value") or {}
        msgs = value.get("messages") or []
        if not msgs:
            return None, None
        msg = msgs[0]
        from_ = msg.get("from") or ""
        texto = (msg.get("text") or {}).get("body", "") if msg.get("type") == "text" else ""
        return from_, texto

    # Evolution API (Baileys) — messages.upsert
    if body.get("event") == "messages.upsert":
        data = body.get("data") or {}
        key = data.get("key") or {}
        if key.get("fromMe"):
            return None, None
        remote_jid = key.get("remoteJid") or ""
        from_ = remote_jid.split("@")[0] if "@" in remote_jid else remote_jid
        msg = data.get("message") or {}
        texto = msg.get("conversation") or ""
        if not texto:
            texto = (msg.get("extendedTextMessage") or {}).get("text") or ""
        return from_, texto

    return None, None


def _send_reply(cfg: dict, from_: str, resposta: str) -> None:
    canal = cfg.get("canal") or "baileys"
    if canal == "meta":
        meta = cfg.get("meta") or {}
        version = meta.get("version") or "v25.0"
        phone = meta.get("phone_number_id") or ""
        token = meta.get("access_token") or ""
        _http_json(
            f"https://graph.facebook.com/{version}/{phone}/messages",
            {"Authorization": "Bearer " + token},
            {"messaging_product": "whatsapp", "to": from_, "type": "text", "text": {"body": resposta}},
        )
    else:  # baileys / Evolution API
        evo = cfg.get("evolution") or {}
        api_url = evo.get("api_url") or "http://localhost:8080"
        api_key = evo.get("api_key") or ""
        instance = evo.get("instance") or "toni"
        _http_json(
            f"{api_url}/message/sendText/{instance}",
            {"apikey": api_key},
            {"number": from_, "text": resposta},
        )


def _send_media(cfg: dict, from_: str, media_url: str, caption: str = "") -> None:
    evo = cfg.get("evolution") or {}
    api_url = evo.get("api_url") or "http://localhost:8080"
    api_key = evo.get("api_key") or ""
    instance = evo.get("instance") or "toni"
    _http_json(
        f"{api_url}/message/sendMedia/{instance}",
        {"apikey": api_key},
        {"number": from_, "mediatype": "image", "media": media_url, "caption": caption},
    )


def _detect_model(resposta: str, modelos: list):
    """Retorna o primeiro modelo mencionado na resposta (nome mais longo primeiro)."""
    if not modelos:
        return None
    ordered = sorted(modelos, key=lambda m: len(m.get("nome") or ""), reverse=True)
    low = (resposta or "").lower()
    for m in ordered:
        nome = (m.get("nome") or "").strip().lower()
        if nome and nome in low:
            return m
    return None


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


# ---- QR code (busca direto da Evolution API, sempre fresco) ----
@app.get("/qr")
def qr():
    evo = _load().get("evolution") or {}
    api_key = evo.get("api_key") or ""
    api_url = evo.get("api_url") or "http://localhost:8080"
    instance = evo.get("instance") or "toni"
    try:
        req = urllib.request.Request(
            f"{api_url}/instance/connect/{instance}",
            headers={"apikey": api_key},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return JSONResponse({"error": "evolution: " + str(e)}, status_code=502)

    b64 = data.get("base64") or ""
    if "," in b64:
        b64 = b64.split(",", 1)[1]
    if not b64:
        return JSONResponse(
            {"error": "sem QR no momento", "state": (data.get("instance") or {}).get("state")},
            status_code=404,
        )
    return Response(content=base64.b64decode(b64), media_type="image/png")


# ---- Webhook do WhatsApp (Meta + Evolution) ----
@app.get("/webhook/whatsapp")
def whatsapp_verify(request: Request):
    q = request.query_params
    mode = q.get("hub.mode") or ""
    token = q.get("hub.verify_token") or ""
    challenge = q.get("hub.challenge") or ""
    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        return Response(content=challenge, media_type="text/plain", status_code=200)
    return Response(content="ok", media_type="text/plain", status_code=200)


@app.post("/webhook/whatsapp")
async def whatsapp_message(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    from_, texto = _extract_message(body)
    print(f"[webhook] from={from_} texto={texto!r}", flush=True)
    if not from_ or not texto:
        return {"ok": True, "status": "no_text"}

    cfg = _load()
    llm = cfg.get("llm") or {}

    if _is_forget(texto):
        _history_clear(from_)
        confirm = "Pronto! Esqueci nossa conversa anterior. Vamos recomeçar: que tipo de negócio você tem?"
        print(f"[forget] histórico apagado para {from_}", flush=True)
        try:
            _send_reply(cfg, from_, confirm)
            return {"ok": True, "status": "forgot"}
        except Exception as e:
            return JSONResponse({"ok": False, "erro": "send: " + str(e)}, status_code=502)

    base = llm.get("base_url") or "http://127.0.0.1:8888/v1"
    model = llm.get("model") or "Qwen/Qwen2.5-14B-Instruct-GGUF"
    llm_key = llm.get("api_key") or ""

    _history_append(from_, "user", texto)
    hist = _history_get(from_)
    user_texts = [m["content"] for m in hist if m["role"] == "user"]
    system = _build_system(cfg) + _slots_note(_extract_slots(user_texts))
    messages = [{"role": "system", "content": system}]
    messages += [{"role": m["role"], "content": m["content"]} for m in hist]

    try:
        llm_resp = _http_json(
            base + "/chat/completions",
            {"Authorization": "Bearer " + llm_key},
            {"model": model, "messages": messages, "max_tokens": 512},
        )
    except Exception as e:
        return JSONResponse({"ok": False, "erro": "llm: " + str(e)}, status_code=502)

    choice = (llm_resp.get("choices") or [{}])[0] or {}
    resposta = (choice.get("message") or {}).get("content", "").strip()
    if not resposta:
        resposta = (choice.get("message") or {}).get("reasoning_content", "").strip()
    if not resposta:
        return {"ok": True, "status": "llm_empty"}

    resposta = _strip_markdown(resposta)
    _history_append(from_, "assistant", resposta)

    try:
        canal = cfg.get("canal") or "baileys"
        modelo = _detect_model(resposta, cfg.get("modelos") or [])

        texto = resposta
        for m in cfg.get("modelos") or []:
            url = (m.get("foto") or "").strip()
            if url and url in texto:
                texto = texto.replace(url, "").strip()

        if modelo:
            texto = _apply_model_link(texto, modelo)

        if canal == "baileys" and modelo and (modelo.get("foto") or "").strip():
            _send_media(cfg, from_, modelo["foto"].strip())
        _send_reply(cfg, from_, texto)

        print(f"[reply] to={from_} modelo={(modelo or {}).get('id')} texto={texto!r}", flush=True)
        return {"ok": True, "status": "sent", "modelo": (modelo or {}).get("id"), "resposta": texto}
    except Exception as e:
        return JSONResponse({"ok": False, "erro": "send: " + str(e)}, status_code=502)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
