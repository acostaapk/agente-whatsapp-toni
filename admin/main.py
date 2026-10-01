import json
import base64
import hashlib
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


FOLLOWUP_MINUTES = 10
FOLLOWUP_PATH = BASE / "config" / "followups.json"
FOLLOWUP_TEXT = "Oi! Conseguiu finalizar sua compra? Se travou em alguma parte, me fala que eu te ajudo. 🙂"
FOLLOWUPS = {}


def _followups_load() -> None:
    try:
        if FOLLOWUP_PATH.exists():
            data = json.loads(FOLLOWUP_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                FOLLOWUPS.update(data)
    except Exception as e:
        print(f"[followup] load falhou: {e}", flush=True)


def _followups_save() -> None:
    try:
        tmp = FOLLOWUP_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(FOLLOWUPS, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, FOLLOWUP_PATH)
    except Exception as e:
        print(f"[followup] save falhou: {e}", flush=True)


def _needs_purchase_followup(phone: str) -> bool:
    msgs = _history_get(phone)
    if not msgs or msgs[-1].get("role") != "assistant":
        return False
    last = msgs[-1]
    if time.time() - last.get("ts", 0) < FOLLOWUP_MINUTES * 60:
        return False
    content = last.get("content") or ""
    links = [(m.get("link") or "") for m in (_load().get("modelos") or []) if m.get("link")]
    offered = any(url and url in content for url in links)
    gated = "já fez a compra" in content or "Android ou iPhone" in content
    if not (offered or gated):
        return False
    for m in msgs:
        if m.get("role") == "user" and re.search(
                r"comprei|comprado|finalizei|finalizado|j[áa] pedi|pedido feito|acabei de (comprar|pedir)|pagamento feito",
                m.get("content") or "", re.IGNORECASE):
            return False
    if FOLLOWUPS.get(phone) == last.get("ts"):
        return False
    return True


def _followup_loop() -> None:
    while True:
        time.sleep(60)
        try:
            for phone in list(HISTORY.keys()):
                if _needs_purchase_followup(phone):
                    cfg = _load()
                    _send_reply(cfg, phone, FOLLOWUP_TEXT)
                    _history_append(phone, "assistant", FOLLOWUP_TEXT)
                    msgs = _history_get(phone)
                    FOLLOWUPS[phone] = msgs[-2].get("ts") if len(msgs) >= 2 else time.time()
                    _followups_save()
                    print(f"[followup] ajuda pós-oferta para {phone}", flush=True)
        except Exception as e:
            print(f"[followup] loop falhou: {e}", flush=True)


_followups_load()
threading.Thread(target=_followup_loop, daemon=True).start()


LEAD_PATH = BASE / "config" / "lead_state.json"
FUNNEL_PATH = BASE / "config" / "funnel.jsonl"
LEADS = {}


def _leads_load() -> None:
    try:
        if LEAD_PATH.exists():
            data = json.loads(LEAD_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                LEADS.update(data)
    except Exception as e:
        print(f"[lead] load falhou: {e}", flush=True)


def _leads_save() -> None:
    try:
        tmp = LEAD_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(LEADS, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, LEAD_PATH)
    except Exception as e:
        print(f"[lead] save falhou: {e}", flush=True)


def _log_event(event: str, phone: str, detail: str = "") -> None:
    try:
        ph = hashlib.sha256((phone or "").encode()).hexdigest()[:12]
        with open(FUNNEL_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "phone": ph, "event": event,
                                "detail": detail}, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[funil] log falhou: {e}", flush=True)


INTENT_PATTERNS = [
    ("compra", r"quero comprar|vou comprar|vou querer|vou ficar|fechado|onde compro|como compro|quero uma m[áa]quina|quero pedir|vou levar"),
    ("link", r"me manda o link|manda o link|cad[êe] o link|link de (compra|pagamento)|link pra (comprar|pagar)"),
    ("modelo", None),  # preenchido por _detect_model
    ("preco", r"quanto custa|qual ([ée] o )?(valor|pre[çc]o)|pre[çc]o da|valor da|mais barata|mais em conta|menor valor|gastar o m[íi]nimo"),
    ("desconto", r"desconto|cupom|promo[çc][ãa]o"),
    ("taxa", r"\btaxa[s]?\b"),
    ("suporte", r"entrega|rastre|defeito|quebr|bobina|suporte|garantia|\btroca\b|reembolso|estorno|instalar|ativar|configurar|erro\b"),
    ("posvenda", r"comprei|chegou|pedido|pagamento (feito|aprovado)"),
    ("renda", r"renda|indica|ganhar (dinheiro|renda)"),
    ("reclamacao", r"reclama|insatisfeit|p[ée]ssimo|horr[íi]vel|cancelar|humano|atendente|golpe|engana"),
    ("trocar", r"j[áa] tenho|tenho uma|trocar|minha atual|segunda|outra (m[áa]quina|maquininha)|mais uma"),
    ("comparacao", r"compar|diferen[çc]a|melhor| versus |\b ou \b|\bx\b"),
]

TEMP_MAP = {"compra": "quente", "link": "quente", "modelo": "quente",
            "preco": "morno", "desconto": "morno", "taxa": "morno",
            "comparacao": "morno", "trocar": "morno"}
SCORE_MAP = {"compra": 20, "link": 15, "modelo": 15, "preco": 10,
             "desconto": 10, "taxa": 5, "comparacao": 5}

MOTIVO_PATTERNS = [
    ("trocar_maquininha", r"trocar|substituir|minha atual"),
    ("segunda_maquininha", r"segunda|mais uma|outra pra|pro meu outro"),
    ("reduzir_custos", r"taxa.*alt|car[oa]|barat|econom|sair mais barato"),
    ("mais_mobilidade", r"\brua\b|entrega|delivery|moto|externo"),
    ("precisa_comprovante", r"impresso|recibo|comprovante"),
]


def _detect_intent(texto: str, modelos: list) -> str:
    """Prioridade: pedido explícito (preço/compra/link/…) vence menção a modelo.
    Ex.: "quanto custa a T3?" -> preco (responde o valor primeiro)."""
    low = texto or ""
    for intent, pat in INTENT_PATTERNS:
        if pat and re.search(pat, low, re.IGNORECASE):
            return intent
    if _detect_model(low, modelos or []):
        return "modelo"
    return "info"


def _detect_motivo(texto: str) -> str:
    for motivo, pat in MOTIVO_PATTERNS:
        if re.search(pat, texto or "", re.IGNORECASE):
            return motivo
    return ""


def _lead_update(phone: str, intent: str, motivo: str) -> dict:
    st = LEADS.setdefault(phone, {"intencao": "", "temperatura": "frio", "motivo_compra": "",
                                  "score": 0, "ultimo_evento": "", "ts": 0})
    st["intencao"] = intent
    if intent in TEMP_MAP:
        order = {"frio": 0, "morno": 1, "quente": 2}
        if order.get(TEMP_MAP[intent], 0) >= order.get(st.get("temperatura") or "frio", 0):
            st["temperatura"] = TEMP_MAP[intent]
    if motivo and not st.get("motivo_compra"):
        st["motivo_compra"] = motivo
    st["score"] = min(100, int(st.get("score") or 0) + SCORE_MAP.get(intent, 0))
    st["ts"] = time.time()
    _leads_save()
    return st


def _lead_event(phone: str, event: str) -> None:
    st = LEADS.setdefault(phone, {"intencao": "", "temperatura": "frio", "motivo_compra": "",
                                  "score": 0, "ultimo_evento": "", "ts": 0})
    st["ultimo_evento"] = event
    st["ts"] = time.time()
    _leads_save()


def _choose_model(slots: dict, user_texts: list, modelos: list):
    """Sales Rules Engine (determinístico): devolve o modelo ou None."""
    by_id = {m.get("id"): m for m in (modelos or [])}
    blob = " ".join(user_texts or []).lower()
    if re.search(r"\bt1\b", blob) and "t1" in by_id:
        return by_id["t1"]
    if re.search(r"gastar o m[íi]nimo|menor (investimento|valor|pre[çc]o)|mais barat|"
                 r"economiz|entrada m[íi]nima|investir pouco|sem gastar muito", blob) and "t1" in by_id:
        return by_id["t1"]
    if re.search(r"android|touch|tela", blob) and "t3_smart" in by_id:
        return by_id["t3_smart"]
    if (slots.get("comprovante") or "") == "impresso":
        return by_id.get("t3")
    if (slots.get("comprovante") or "") == "digital":
        return by_id.get("t2")
    return None


def _rec_package(modelo: dict, slots: dict) -> str:
    nome = (modelo.get("nome") or "modelo").strip()
    preco = (modelo.get("preco") or "").strip()
    parcelas = (modelo.get("parcelas") or "em até 12x").strip()
    mid = modelo.get("id") or ""
    if mid == "t3_smart":
        motivo = "ela tem tela Android e comprovante impresso"
    elif (slots.get("comprovante") or "") == "digital":
        motivo = "o comprovante digital já resolve pra você"
    else:
        motivo = "ela tem comprovante impresso"
    out = f"Pelo que você me contou, a {nome} combina com o seu uso porque {motivo}."
    if preco:
        out += (f"\n\nA {nome} é adesão única de {preco} (catálogo 23/09/2026), "
                f"parcelável {parcelas}, sem mensalidade.")
    out += ("\n\nNos primeiros 30 dias ou até R$5 mil em vendas, débito 0,57% e crédito à vista 0,57%."
            "\n\n" + _recommendation_fallback(modelo, slots))
    return out


def _price_overview(cfg: dict) -> str:
    """Pergunta de preço sem modelo específico: informa a faixa e conduz à microqualificação."""
    modelos = cfg.get("modelos") or []

    def _val(m):
        d = re.sub(r"[^\d,]", "", (m.get("preco") or "").replace(".", ""))
        return float(d.replace(",", ".")) if d else 0.0

    com_preco = sorted([m for m in modelos if m.get("preco")], key=_val)
    if not com_preco:
        return "Deixa eu confirmar os valores atuais pra te passar certinho. Você precisa de comprovante impresso?"
    linhas = "\n".join(f"- {m.get('nome')}: {m.get('preco')} ({m.get('parcelas') or 'em até 12x'})"
                       for m in com_preco)
    return (f"Hoje temos modelos a partir de {com_preco[0].get('preco')} (adesão única, sem mensalidade — "
            f"catálogo 23/09/2026):\n{linhas}\n\n"
            "Você precisa de comprovante impresso ou o digital já resolve? Assim eu já te indico o modelo certo.")


def _deliver_offer(cfg: dict, from_: str, modelo: dict, slots: dict, hist: list) -> str:
    """Envia o pacote da oferta (texto + foto/link + dúvida) e registra no histórico. Devolve o texto."""
    texto = _ensure_pix_note(_rec_package(modelo, slots), modelo)
    texto = _apply_model_link(texto, modelo)
    for bubble in _split_bubbles(texto):
        _send_reply(cfg, from_, bubble)
    canal = cfg.get("canal") or "baileys"
    if canal == "baileys" and modelo and not _already_offered(hist, cfg.get("modelos") or []):
        nome = (modelo.get("nome") or "modelo").strip()
        foto = (modelo.get("foto") or "").strip()
        link = (modelo.get("link") or "").strip()
        if foto:
            caption = f"{nome} — peça pelo link: {link}" if link else nome
            _send_media(cfg, from_, foto, caption)
        _send_voice_or_text(cfg, from_, DOUBT_CHECK)
    _history_append(from_, "assistant", texto)
    _lead_event(from_, "oferta")
    _log_event("oferta", from_, modelo.get("id") or "")
    return texto


def _lead_note(st: dict) -> str:
    if not st:
        return ""
    return (f"\n\n# LEAD (estado comercial)\nIntenção: {st.get('intencao') or '—'} | "
            f"Temperatura: {st.get('temperatura') or 'frio'} | Motivo: {st.get('motivo_compra') or '—'} | "
            f"Score: {st.get('score') or 0} | Último evento: {st.get('ultimo_evento') or '—'}\n"
            "Quente (compra/link/modelo): responda e conclua, sem enrolar. "
            "Morno (preço/taxa/desconto): responda o dado + 1 pergunta. Frio: oriente.")


_leads_load()


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
        if "celular_so" not in slots:
            if re.search(r"iphone|\bios\b|ipad", low) or re.search(r"apple(?! pay)", low):
                slots["celular_so"] = "ios"
            elif re.search(r"android|samsung|galaxy|motorola|xiaomi|pixel|lenovo|lg\b", low):
                slots["celular_so"] = "android"
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


def _apply_app_link(texto: str, cfg: dict, slots: dict = None, hist: list = None) -> str:
    """Anexa o link do app da LOJA CERTA (App Store/Play), mas SÓ se a compra foi confirmada."""
    slots = slots or {}
    hist = hist or []
    if not re.search(r"link do app|link do aplicativo|baixar o app|baixe o app|app ton|app da ton|aplicativo ton|aplicativo da ton", texto, re.IGNORECASE):
        return texto.strip()
    universal = (cfg.get("link_app") or "").strip()
    ios = (cfg.get("link_app_ios") or "").strip() or universal
    android = (cfg.get("link_app_android") or "").strip() or universal
    if (universal and universal in texto) or (ios and ios in texto) or (android and android in texto):
        return texto.strip()
    asst = [m.get("content") or "" for m in hist if m.get("role") == "assistant"]
    users = [m.get("content") or "" for m in hist if m.get("role") == "user"]
    purchased = bool(re.search(r"comprei|comprado|finalizei|finalizado|j[áa] pedi|pedido feito|acabei de (comprar|pedir)|pagamento feito", users[-1] if users else "", re.IGNORECASE))
    if not purchased:
        if any("já fez a compra" in a for a in asst):
            return texto.strip()
        return texto.rstrip() + "\n\nVocê já fez a compra? Assim te mando o link do app. 🙂"
    so = (slots.get("celular_so") or "").strip()
    if so == "ios":
        return texto.rstrip() + "\n\n" + ios
    if so == "android":
        return texto.rstrip() + "\n\n" + android
    if any("Android ou iPhone" in a for a in asst):
        return texto.rstrip() + "\n\n" + universal
    return texto.rstrip() + "\n\nSeu celular é Android ou iPhone? Assim te mando o link certo da loja. 🙂"


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
    parcelas = (last_modelo.get("parcelas") or "em até 12x").strip()
    return f"A {last_modelo.get('nome')} é adesão única de {preco} (catálogo 23/09/2026), parcelável {parcelas}, sem mensalidade."


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
             "comprovante": "Comprovante", "mobilidade": "Mobilidade",
             "nome": "Nome", "celular_so": "Celular"}
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
            if audio:
                print(f"[stt-debug] audio keys={list(audio.keys())} "
                      f"fileLength={audio.get('fileLength')} mimetype={audio.get('mimetype')}", flush=True)
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


def _extract_msg_id(body) -> str:
    try:
        if body.get("event") == "messages.upsert":
            return ((body.get("data") or {}).get("key") or {}).get("id") or ""
    except Exception:
        pass
    return ""


def _fetch_media_b64(cfg: dict, msg_id: str):
    """Pede ao Evolution o base64 decifrado da mídia (getBase64FromMediaMessage)."""
    evo = cfg.get("evolution") or {}
    api_url = evo.get("api_url") or "http://localhost:8080"
    api_key = evo.get("api_key") or ""
    instance = evo.get("instance") or "toni"
    r = _http_json(
        f"{api_url}/chat/getBase64FromMediaMessage/{instance}",
        {"apikey": api_key},
        {"message": {"key": {"id": msg_id}}, "convertToMp4": False},
    )

    def hunt(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k.lower() in ("base64", "data", "media") and isinstance(v, str) and len(v) > 1000:
                    return v
                h = hunt(v)
                if h:
                    return h
        elif isinstance(o, list):
            for v in o:
                h = hunt(v)
                if h:
                    return h
        return None

    b64 = hunt(r) or ""
    if "," in b64 and len(b64) > 1100:
        b64 = b64.split(",", 1)[1]
    return b64


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
    msg_id = _extract_msg_id(body) if (from_ and not texto) else ""
    if (audio_url or msg_id) and not audio_b64 and msg_id:
        try:
            audio_b64 = _fetch_media_b64(_load(), msg_id)
            print(f"[stt] getBase64: {'ok' if audio_b64 else 'vazio'} ({len(audio_b64)} chars)", flush=True)
        except Exception as e:
            print(f"[stt] getBase64 falhou: {e}", flush=True)
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
    
    # Toní 3.0 - Estado comercial + intenção (antes de responder)
    intent = _detect_intent(texto, cfg.get("modelos") or [])
    motivo = _detect_motivo(texto)
    lead = _lead_update(from_, intent, motivo)
    _log_event("mensagem", from_, intent)
    
    system = _build_system(cfg) + _slots_note(slots) + _lead_note(lead)
    messages = [{"role": "system", "content": system}]
    messages += [{"role": m["role"], "content": m["content"]} for m in hist]
    
    # ===== MODO LEAD QUENTE (Toní 3.0): responder a intenção ANTES de qualificar =====
    modelos = cfg.get("modelos") or []
    modelo_pre = _detect_model(texto, modelos)

    def _reply_simple(msg, status, evento=""):
        try:
            for bubble in _split_bubbles(msg):
                _send_reply(cfg, from_, bubble)
            _history_append(from_, "assistant", msg)
            if evento:
                _lead_event(from_, evento)
                _log_event(evento, from_, "")
            return {"ok": True, "status": status, "resposta": msg}
        except Exception as e:
            return JSONResponse({"ok": False, "erro": "send: " + str(e)}, status_code=502)

    # Cold-open (1ª mensagem): se já é uma objeção comercial, responde direto, sem auto-apresentação
    if len(user_texts) == 1 and intent == "trocar":
        return _reply_simple(
            "Entendi! Já que você tem uma maquininha, me conta: o que te faz pensar em trocar? "
            "(taxa, mobilidade, comprovante ou uma segunda máquina) Assim eu já te indico o melhor caminho. 🙂",
            "objecao_trocar",
        )

    # Perguntou preço (com ou sem modelo): responde o valor primeiro
    if intent == "preco":
        if modelo_pre:
            try:
                txt = _deliver_offer(cfg, from_, modelo_pre, slots, hist)
                return {"ok": True, "status": "hot_preco_modelo",
                        "modelo": modelo_pre.get("id"), "resposta": txt}
            except Exception as e:
                return JSONResponse({"ok": False, "erro": "send: " + str(e)}, status_code=502)
        return _reply_simple(_price_overview(cfg), "hot_preco_faixa", "oferta_faixa")

    # Quer comprar e já deu o essencial (modelo citado ou comprovante): entrega já
    if intent in ("compra", "modelo"):
        alvo = modelo_pre or (_choose_model(slots, user_texts, modelos)
                              if (slots.get("comprovante") or "") else None)
        if alvo:
            try:
                txt = _deliver_offer(cfg, from_, alvo, slots, hist)
                return {"ok": True, "status": "hot_compra_modelo",
                        "modelo": alvo.get("id"), "resposta": txt}
            except Exception as e:
                return JSONResponse({"ok": False, "erro": "send: " + str(e)}, status_code=502)


    # Raciocínio só nos casos complexos: simples responde rápido, difícil responde certo
    complex_case = len(slots) >= 3 or len(texto) > 150 or len(hist) > 10
    print(f"[llm] thinking={complex_case} slots={list(slots)} intent={intent}", flush=True)
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
    cold_commercial = is_fresh and intent not in ("info", "")
    resposta = _strip_reintro(resposta, not cold_commercial)
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

        # Toní 3.0 (regra 8): pós-venda/suporte/reclamação NÃO executa fluxo de vendas
        # (sem link de compra, sem Pix, sem fechamento de recomendação).
        nao_vende = intent in ("posvenda", "suporte", "reclamacao")
        if modelo and not nao_vende:
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
        texto = _apply_app_link(texto, cfg, slots, hist)

        for bubble in _split_bubbles(texto):
            _send_reply(cfg, from_, bubble)
        if canal == "baileys" and modelo and not nao_vende and not _already_offered(hist, cfg.get("modelos") or []):
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
