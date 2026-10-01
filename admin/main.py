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

GREETING_EXACT = ("oi", "olá", "ola", "bom dia", "boa tarde", "boa noite", "opa", "e aí", "e ai",
                  "hello", "hi", "começar", "comecar", "início", "inicio")

CANNED_GREETING = ("Oi! Sou o Toni, parceiro Ton. Tenho 4 opções de maquininhas e posso te ajudar "
                   "a escolher o modelo que faz mais sentido pra você. Que tipo de negócio você tem?")

CRIME_PAT = r"tr[áa]fico|traficante|lavar dinheiro|lavagem de dinheiro|branqueamento|recepta|estelionato|fraude|contrabando|armas?\b"

CANNED_REFUSAL = "Não consigo ajudar com isso. Vou te encaminhar pra nossa equipe."

NO_INTEREST_PAT = r"n[ãa]o tenho interesse|sem interesse|j[áa] tenho maquininha|j[áa] tenho m[áa]quina|deixa pra l[áa]|deixa quieto|dispenso|n[ãa]o,?\s*obrigad"
INTEREST_ESCAPE_PAT = r"\bmas\b|\?|taxa|quanto|vale a pena|compar|quero saber|me conta|me fala"

CANNED_EXIT = ("Tranquilo, entendo! Só reforço que a Ton tem Pix 0% com chave cadastrada e sem mensalidade "
               "— se mudar de ideia, tô por aqui. Obrigado pela atenção! 🙂")

DOUBT_CHECK = "Você tem alguma dúvida até aqui? 🙂"


def _is_greeting(texto: str) -> bool:
    return _norm(texto).strip("!., ") in GREETING_EXACT


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


DISCOUNT_CANONICAL = ("Pode existir desconto na adesão pelo nosso link, conforme a condição disponível "
                      "no momento. Não é um valor garantido e não há custo extra para você.")


def _ensure_discount_phrase(texto: str) -> str:
    """Troca qualquer frase de desconto fora do padrão pela frase canônica."""
    if "descont" not in texto.lower() or "pode existir desconto" in texto.lower():
        return texto
    sents = _split_sentences(texto)
    out = []
    swapped = False
    for s in sents:
        if not swapped and "descont" in s.lower():
            out.append(DISCOUNT_CANONICAL)
            swapped = True
        else:
            out.append(s)
    return " ".join(out).strip()


def _strip_markdown(texto: str) -> str:
    texto = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", texto)  # remove imagens markdown (![]())
    texto = re.sub(r"\[([^\]]*)\]\((https?://[^)]+)\)", r"\1 \2", texto)  # [texto](url) -> texto url
    texto = re.sub(r"\([^)]*automaticamente[^)]*\)", "", texto)  # remove vazamento de mecanismo
    texto = re.sub(r"Quer que eu te mostre como fica a taxa[^\n?]*\?", "", texto, flags=re.IGNORECASE)  # pergunta banida
    texto = re.sub(r"Quer que eu te [^\n?]*\?", "", texto, flags=re.IGNORECASE)  # "quer que eu te...?" nunca
    texto = re.sub(r",?\s*(e )?o prazo varia conforme a regi[ãa]o[.?!]?", ".", texto, flags=re.IGNORECASE)  # prazo inventado
    texto = re.sub(r"\s*(e |, )?(é (bem |super |muito )?[^.?!]{0,60}?)?pra quem [^.?!]*(crescendo|crescer|vendendo mais|vender mais|faturando|faturar mais|expandindo|expandir|escalando|escalar)[^.?!]*[.?!]?", "", texto, flags=re.IGNORECASE)  # necessidade presumida
    texto = _ensure_discount_phrase(texto)
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
        if "nome" not in slots:
            m = (re.search(r"me chamo\s+([A-ZÀ-Ú][a-zà-ú]+)", t or "")
                 or re.search(r"meu nome [ée]\s+([A-ZÀ-Ú][a-zà-ú]+)", t or "")
                 or re.search(r"\bsou\s+[oa]\s+([A-ZÀ-Ú][a-zà-ú]+)", t or ""))
            if m and m.group(1).lower() not in ("toni", "ton"):
                slots["nome"] = m.group(1).strip()
    return slots


SLOT_QUESTION_PATTERNS = {
    "negocio": [r"que tipo de neg[óo]cio"],
    "mobilidade": [r"celular por perto", r"funcione sozinha"],
    "comprovante": [r"comprovante impresso ou", r"impresso ou o digital"],
    "volume": [r"faixa de vendas"],
}

CADASTRO_QUESTION_PAT = r"cpf.{0,30}mei.{0,30}cnpj|cnpj.{0,30}mei|mei ou cnpj|cpf ou cnpj|no cpf"


def _strip_cadastro_question(texto: str, slots: dict) -> str:
    """NUNCA perguntar CPF/MEI/CNPJ: remove essas perguntas sempre, mesmo sem dado conhecido."""
    kept = [p for p in _split_sentences(texto)
            if not (re.search(r"\?", p) and re.search(CADASTRO_QUESTION_PAT, p, re.IGNORECASE))]
    out = "\n\n".join(kept).strip()
    if not out:
        out = _fallback_question(slots)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def _apply_app_link(texto: str, cfg: dict) -> str:
    """Se a resposta fala do link do app mas não traz URL, anexa o link_app."""
    app_link = (cfg.get("link_app") or "").strip()
    if not app_link or app_link in texto:
        return texto
    if re.search(r"link do app|link do aplicativo|baixar o app|baixe o app|app ton|app da ton|aplicativo ton|aplicativo da ton", texto, re.IGNORECASE):
        texto = texto.rstrip() + "\n\n" + app_link
    return texto.strip()


def _split_bubbles(texto: str) -> list:
    """Divide a resposta em bolhas (1 parágrafo = 1 mensagem). Bolha só-emoji gruda na anterior."""
    parts = [p.strip() for p in re.split(r"\n{2,}", texto) if p.strip()]
    merged = []
    for p in parts:
        if merged and not re.search(r"[A-Za-z0-9]", p) and len(p) <= 8:
            merged[-1] = merged[-1] + " " + p
        else:
            merged.append(p)
    return merged or [texto]


def _soften_superlative(texto: str) -> str:
    texto = re.sub(r"\s+[ée] a melhor op[cç][aã]o para você", " combina com o seu uso", texto, flags=re.IGNORECASE)
    return texto


def _split_sentences(texto: str) -> list:
    return [p for p in re.split(r"(?<=[.?!])\s+|\n+", texto) if p.strip()]


INTRO_PAT = r"sou o toni\b|meu nome [ée] toni\b|me chamo toni\b"
GREETING_PAT = r"^(oi|ol[aá]|opa|e a[ií]|bom dia|boa tarde|boa noite)[!.,\s]*$"


def _strip_reintro(texto: str, is_fresh: bool) -> str:
    """Remove auto-apresentação e saudações soltas quando a conversa já começou."""
    if is_fresh:
        return texto
    kept = [p for p in _split_sentences(texto)
            if not re.search(INTRO_PAT, p, re.IGNORECASE)
            and not re.match(GREETING_PAT, p.strip(), re.IGNORECASE)]
    return "\n\n".join(kept).strip()


def _fallback_question(slots: dict) -> str:
    if "negocio" not in slots:
        return "Que tipo de negócio você tem?"
    neg = str(slots["negocio"]).capitalize()
    if "comprovante" not in slots:
        return f"{neg}, anotado! Seu cliente precisa de comprovante impresso ou o digital já resolve?"
    if "volume" not in slots:
        return "Qual é a sua faixa de vendas por mês?"
    return "Anotado! Se precisar de mais alguma informação, tô por aqui."


MASC_A = ("lava", "mapa", "sofá", "sofa")
FEM_E = ("lanchonete",)


def _artigo_negocio(neg: str) -> str:
    """'pra sua sapataria' / 'pro seu ferro velho' — nunca 'pro seu sapataria'."""
    first = (neg or "").strip().split(" ")[0].lower()
    if first in FEM_E or (first.endswith("a") and first not in MASC_A):
        return f"pra sua {neg}"
    return f"pro seu {neg}"


def _recommendation_fallback(modelo: dict, slots: dict) -> str:
    nome = ((modelo or {}).get("nome") or "modelo").strip()
    neg = str(slots.get("negocio") or "").strip()
    who = str(slots.get("nome") or "").strip()
    alvo = f", a ideal {_artigo_negocio(neg)}" if neg else ", a ideal pra você"
    thanks = f"Obrigado, {who}!" if who else "Obrigado!"
    return f"Tô te enviando o link da {nome}{alvo}. {thanks} Se precisar, tô por aqui."


PIX_STANDARD = "Pix 0% com chave cadastrada na Conta Ton (30 dias grátis pra novos clientes; sem chave, 0,49% por venda)."
PIX_T1_NONE = "A T1 não tem Pix na maquininha."


def _ensure_pix_note(texto: str, modelo: dict) -> str:
    """Garante a frase de Pix: T1 nega; demais modelos afirmam com condições."""
    if not modelo:
        return texto
    mid = (modelo.get("id") or "").strip()
    low = texto.lower()
    if mid == "t1":
        if re.search(r"n[ãa]o tem pix|sem pix", low):
            return texto
        kept = [s for s in _split_sentences(texto) if "pix" not in s.lower()]
        texto = " ".join(kept).strip()
        if PIX_T1_NONE.lower() not in texto.lower():
            texto = texto.rstrip() + "\n\n" + PIX_T1_NONE
        return texto
    if "pix" not in low:
        texto = texto.rstrip() + "\n\n" + PIX_STANDARD
    return texto


OBJECTION_PAT = r"t[áa] caro|muito caro|tem desconto|é caro|quanto custa|\bpre[çc]o\b|\bvalor\b"


def _price_anchor(user_last: str, last_modelo: dict) -> str:
    """Objeção de preço sem reancoragem no texto: devolve a linha de valor ou ''."""
    if not last_modelo:
        return ""
    if not re.search(OBJECTION_PAT, user_last or "", re.IGNORECASE):
        return ""
    preco = (last_modelo.get("preco") or "").strip()
    if not preco:
        return ""
    return f"A {last_modelo.get('nome')} é adesão única de {preco} (catálogo 23/09/2026), parcelável em até 12x, sem mensalidade."


def _strip_all_questions(texto: str) -> str:
    """Na recomendação: remove TODAS as frases de pergunta."""
    kept = [p for p in _split_sentences(texto) if "?" not in p]
    return "\n\n".join(kept).strip()


def _keep_first_question(texto: str) -> str:
    """Garante no máximo 1 frase de pergunta por mensagem."""
    out, seen_q = [], False
    for p in _split_sentences(texto):
        if re.search(r"\?", p):
            if seen_q:
                continue
            seen_q = True
        out.append(p)
    return "\n\n".join(out).strip()


def _strip_repeated_questions(texto: str, slots: dict) -> str:
    """Remove frases de pergunta sobre dados que o cliente já informou."""
    if not slots:
        return texto
    pats = []
    for k in slots:
        pats += SLOT_QUESTION_PATTERNS.get(k, [])
    if not pats:
        return texto
    kept = [p for p in _split_sentences(texto)
            if not (re.search(r"\?", p) and any(re.search(pat, p, re.IGNORECASE) for pat in pats))]
    out = "\n\n".join(kept).strip()
    if not out:
        out = _fallback_question(slots)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def _slots_note(slots: dict) -> str:
    if not slots:
        return ""
    nomes = {"negocio": "Tipo de negócio", "volume": "Vendas mensais",
             "comprovante": "Comprovante", "mobilidade": "Mobilidade"}
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
    sm = ""
    prompt_file = BASE / "prompts" / "toni-system.md"
    if prompt_file.exists():
        sm = prompt_file.read_text(encoding="utf-8")
        sm = re.sub(r"<!--.*?-->", "", sm, flags=re.DOTALL).strip()
    if not sm.strip():
        sm = cfg.get("system_message") or ""  # fallback legado
    repl = {
        "{{agente_nome}}": cfg.get("agente_nome") or "",
        "{{empresa_nome}}": cfg.get("empresa_nome") or "",
        "{{servico_principal}}": cfg.get("servico_principal") or "",
        "{{criterio_alto_valor}}": cfg.get("criterio_alto_valor") or "",
        "{{link_app}}": cfg.get("link_app") or "",
        "{{base_conhecimento}}": cfg.get("base_conhecimento") or "",
    }
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


def _send_media(cfg: dict, from_: str, media_url: str, caption: str = "", mediatype: str = "image") -> None:
    evo = cfg.get("evolution") or {}
    api_url = evo.get("api_url") or "http://localhost:8080"
    api_key = evo.get("api_key") or ""
    instance = evo.get("instance") or "toni"
    _http_json(
        f"{api_url}/message/sendMedia/{instance}",
        {"apikey": api_key},
        {"number": from_, "mediatype": mediatype, "media": media_url, "caption": caption},
    )


def _send_voice(cfg: dict, from_: str, ogg_path: str) -> None:
    import base64
    evo = cfg.get("evolution") or {}
    api_url = evo.get("api_url") or "http://localhost:8080"
    api_key = evo.get("api_key") or ""
    instance = evo.get("instance") or "toni"
    with open(ogg_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    _http_json(
        f"{api_url}/message/sendWhatsAppAudio/{instance}",
        {"apikey": api_key},
        {"number": from_, "audio": b64},
    )


def _send_voice_or_text(cfg: dict, from_: str, texto: str) -> None:
    """Voz em vez de texto nos momentos de conexão; cai pra texto se o TTS falhar."""
    try:
        from tts import tts_ogg
        ogg = tts_ogg(texto)
    except Exception as e:
        print(f"[tts] indisponível: {e}", flush=True)
        ogg = None
    if ogg:
        print(f"[voice] áudio para {from_}", flush=True)
        _send_voice(cfg, from_, ogg)
    else:
        _send_reply(cfg, from_, texto)


def _extract_audio_url(body) -> str:
    """URL do áudio (Evolution/Baileys) ou ''."""
    try:
        if body.get("event") == "messages.upsert":
            data = body.get("data") or {}
            msg = data.get("message") or {}
            audio = msg.get("audioMessage") or msg.get("pttMessage") or {}
            return audio.get("url") or ""
    except Exception:
        pass
    return ""


def _extract_audio_b64(body) -> str:
    """Áudio já decifrado (base64 do Evolution) ou ''."""
    try:
        if body.get("event") == "messages.upsert":
            data = body.get("data") or {}
            msg = data.get("message") or {}
            b64 = msg.get("base64") or ""
            if b64 and len(b64) > 1000:
                return b64
    except Exception:
        pass
    return ""


def _already_offered(hist: list, modelos: list) -> bool:
    """True se alguma resposta anterior já continha link de modelo (oferta já feita)."""
    links = [m.get("link") or "" for m in (modelos or []) if m.get("link")]
    for h in hist or []:
        if h.get("role") != "assistant":
            continue
        content = h.get("content") or ""
        if any(url and url in content for url in links):
            return True
    return False


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
    audio_url = _extract_audio_url(body) if (from_ and not texto) else ""
    audio_b64 = _extract_audio_b64(body) if (from_ and not texto) else ""
    if from_ and not texto and not audio_url and not audio_b64:
        try:
            dbg = ((body.get("data") or {}).get("message") or {})
            print(f"[stt-debug] msg keys={list(dbg.keys())}", flush=True)
        except Exception:
            pass
    if audio_b64 or audio_url:
        try:
            import base64
            from stt import transcribe_audio_url, transcribe_bytes
            if audio_b64:
                print(f"[stt] via base64 ({len(audio_b64)} chars)", flush=True)
                texto = transcribe_bytes(base64.b64decode(audio_b64))
            else:
                texto = transcribe_audio_url(audio_url)
            print(f"[stt] transcrito: {texto!r}", flush=True)
        except Exception as e:
            print(f"[stt] indisponível: {e}", flush=True)
            texto = ""
    if not from_ or not texto:
        if audio_b64 or audio_url:
            try:
                _send_reply(_load(), from_, "Não consegui ouvir seu áudio, pode escrever pra mim? 🙂")
            except Exception as e:
                print(f"[stt] fallback falhou: {e}", flush=True)
        return {"ok": True, "status": "no_text"}

    cfg = _load()
    llm = cfg.get("llm") or {}

    if _is_forget(texto):
        _history_clear(from_)
        confirm = "Pronto, esqueci nossa conversa anterior. Se precisar de ajuda com maquininha, é só chamar! 👋"
        print(f"[forget] histórico apagado para {from_}", flush=True)
        try:
            _send_reply(cfg, from_, confirm)
            return {"ok": True, "status": "forgot"}
        except Exception as e:
            return JSONResponse({"ok": False, "erro": "send: " + str(e)}, status_code=502)

    if (not _history_get(from_) and _is_greeting(texto)
            and not _detect_model(texto, cfg.get("modelos") or [])
            and not _extract_slots([texto])):
        _history_append(from_, "user", texto)
        _history_append(from_, "assistant", CANNED_GREETING)
        print(f"[greet] saudação padrão para {from_}", flush=True)
        try:
            _send_voice_or_text(cfg, from_, CANNED_GREETING)
            return {"ok": True, "status": "greeted"}
        except Exception as e:
            return JSONResponse({"ok": False, "erro": "send: " + str(e)}, status_code=502)

    if re.search(CRIME_PAT, texto or "", re.IGNORECASE):
        _history_append(from_, "user", texto)
        _history_append(from_, "assistant", CANNED_REFUSAL)
        print(f"[crime] recusa + escalada para {from_}", flush=True)
        try:
            _send_voice_or_text(cfg, from_, CANNED_REFUSAL)
            return {"ok": True, "status": "refused_crime"}
        except Exception as e:
            return JSONResponse({"ok": False, "erro": "send: " + str(e)}, status_code=502)

    if (re.search(NO_INTEREST_PAT, texto or "", re.IGNORECASE)
            and not re.search(INTEREST_ESCAPE_PAT, texto or "", re.IGNORECASE)):
        _history_append(from_, "user", texto)
        _history_append(from_, "assistant", CANNED_EXIT)
        print(f"[no-interest] saída elegante para {from_}", flush=True)
        try:
            _send_voice_or_text(cfg, from_, CANNED_EXIT)
            return {"ok": True, "status": "no_interest"}
        except Exception as e:
            return JSONResponse({"ok": False, "erro": "send: " + str(e)}, status_code=502)

    base = llm.get("base_url") or "http://127.0.0.1:8888/v1"
    model = llm.get("model") or "Qwen/Qwen2.5-14B-Instruct-GGUF"
    llm_key = llm.get("api_key") or ""

    _history_append(from_, "user", texto)
    hist = _history_get(from_)
    user_texts = [m["content"] for m in hist if m["role"] == "user"]
    slots = _extract_slots(user_texts)
    system = _build_system(cfg) + _slots_note(slots)
    messages = [{"role": "system", "content": system}]
    messages += [{"role": m["role"], "content": m["content"]} for m in hist]

    # Raciocínio só nos casos complexos: simples responde rápido, difícil responde certo
    complex_case = len(slots) >= 3 or len(texto) > 150 or len(hist) > 10
    print(f"[llm] thinking={complex_case} slots={list(slots)}", flush=True)
    try:
        llm_resp = _http_json(
            base + "/chat/completions",
            {"Authorization": "Bearer " + llm_key},
            {"model": model, "messages": messages, "max_tokens": 4096, "enable_thinking": complex_case},
        )
    except Exception as e:
        return JSONResponse({"ok": False, "erro": "llm: " + str(e)}, status_code=502)

    choice = (llm_resp.get("choices") or [{}])[0] or {}
    resposta = (choice.get("message") or {}).get("content", "").strip()
    # NUNCA enviar reasoning interno ao cliente: se o conteúdo veio vazio, sem resposta
    if not resposta:
        return {"ok": True, "status": "llm_empty"}

    resposta = _strip_markdown(resposta)
    resposta = _soften_superlative(resposta)
    is_fresh = len([m for m in hist if m["role"] == "user"]) <= 1
    resposta = _strip_reintro(resposta, is_fresh)
    resposta = _strip_repeated_questions(resposta, slots)
    resposta = _strip_cadastro_question(resposta, slots)
    resposta = _keep_first_question(resposta)
    modelo_pre = _detect_model(resposta, cfg.get("modelos") or [])
    if modelo_pre:
        resposta = _strip_all_questions(resposta)
        if not resposta:
            resposta = _recommendation_fallback(modelo_pre, slots)
    if not resposta:
        resposta = _fallback_question(slots)

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
            texto = _ensure_pix_note(texto, modelo)
            low = texto.lower()
            if not any(w in low for w in ("obrigado", "valeu", "por aqui", "disposi", "precisando")):
                texto = texto.rstrip() + "\n\n" + _recommendation_fallback(modelo, slots)
        user_last = next((m["content"] for m in reversed(hist) if m["role"] == "user"), "")
        last_modelo = modelo
        if not last_modelo:
            for m in reversed(hist):
                if m["role"] == "assistant":
                    last_modelo = _detect_model(m["content"], cfg.get("modelos") or [])
                    if last_modelo:
                        break
        if last_modelo and "R$" not in texto:
            anchor = _price_anchor(user_last, last_modelo)
            if anchor:
                texto = anchor + "\n\n" + texto
        texto = _apply_app_link(texto, cfg)

        for bubble in _split_bubbles(texto):
            _send_reply(cfg, from_, bubble)
        if canal == "baileys" and modelo and not _already_offered(hist, cfg.get("modelos") or []):
            nome = ((modelo.get("nome") or "modelo").strip())
            foto = (modelo.get("foto") or "").strip()
            link = (modelo.get("link") or "").strip()
            if foto:
                caption = f"{nome} — peça pelo link: {link}" if link else nome
                _send_media(cfg, from_, foto, caption)
            _send_voice_or_text(cfg, from_, DOUBT_CHECK)

        print(f"[reply] to={from_} modelo={(modelo or {}).get('id')} texto={texto!r}", flush=True)
        _history_append(from_, "assistant", texto)
        return {"ok": True, "status": "sent", "modelo": (modelo or {}).get("id"), "resposta": texto}
    except Exception as e:
        _history_append(from_, "assistant", resposta)
        return JSONResponse({"ok": False, "erro": "send: " + str(e)}, status_code=502)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
