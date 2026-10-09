import json
import base64
import hashlib
import hmac
import os
import random
import re
import secrets
import sqlite3
import time
import threading
import unicodedata
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from dedupe import Dedupe
from followup_policy import (
    FOLLOWUP_MINUTES,
    purchase_check_due,
)
from sales_policy import (
    recommend_model,
    same_cnpj_ton_restricted,
)

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config" / "agent-config.json"
EXAMPLE_PATH = BASE / "config" / "agent-config.example.json"
STATIC_DIR = BASE / "static"
QR_PATH = Path("/home/andre/marketing/agente-whatsapp/infra/qr-toni.png")

app = FastAPI(title="Admin — Agente WhatsApp Toní")

VERIFY_TOKEN = "toni-verify-2026"
USERS_DB_PATH = BASE / "config" / "users.db"
SESSION_TTL = 30 * 24 * 3600  # 30 dias
HUMAN_SUPPORT_REPLIES_PATH = BASE / "config" / "human-support-replies.json"
HUMAN_SUPPORT_REPLIES_LOCK = threading.Lock()
HUMAN_SUPPORT_DEFAULT_REPLIES = [
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
HUMAN_SUPPORT_ASSETS = [
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
]
HUMAN_SUPPORT_ASSET_IDS = frozenset(asset["id"] for asset in HUMAN_SUPPORT_ASSETS)


def _human_support_read_library() -> dict:
    if not HUMAN_SUPPORT_REPLIES_PATH.exists():
        return {"version": 0, "replies": [dict(reply) for reply in HUMAN_SUPPORT_DEFAULT_REPLIES]}
    data = json.loads(HUMAN_SUPPORT_REPLIES_PATH.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("version"), int)
        or isinstance(data.get("version"), bool)
        or data["version"] < 0
        or not isinstance(data.get("replies"), list)
    ):
        raise ValueError("biblioteca inválida")
    return {"version": data["version"], "replies": data["replies"]}


def _human_support_validate_replies(replies):
    if not isinstance(replies, list) or len(replies) > 100:
        return None

    normalized = []
    seen_ids = set()
    for reply in replies:
        if not isinstance(reply, dict):
            return None
        reply_id = reply.get("id")
        if (
            not isinstance(reply_id, str)
            or len(reply_id) > 40
            or not re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", reply_id)
        ):
            return None
        if reply_id in seen_ids:
            return None
        seen_ids.add(reply_id)

        fields = {}
        for field, max_length in (("category", 40), ("label", 60), ("text", 2000)):
            value = reply.get(field)
            if not isinstance(value, str):
                return None
            value = value.strip()
            if not value or len(value) > max_length:
                return None
            fields[field] = value
        normalized.append({"id": reply_id, **fields})
    return normalized

# Conversa por número (persistida em disco; uvicorn roda com 1 worker)
HISTORY_PATH = BASE / "config" / "history.json"
HISTORY = {}
HISTORY_LOCK = threading.Lock()
MAX_HISTORY = 20
HISTORY_TTL = 48 * 3600  # 48h
MAX_PHONES = 500

# Ledger de compliance/aprendizado: append-only, nunca é cortado (o history.json acima é
# só contexto do LLM, efêmero). Uma linha por mensagem com etiquetas de telefone, data,
# hora, intent e origem — origin/intent valem por thread, setados nos pontos de entrada.
LEDGER_PATH = BASE / "config" / "messages.jsonl"
LEDGER_LOCK = threading.Lock()
LEDGER_TZ = ZoneInfo("America/Sao_Paulo")
_CTX = threading.local()


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


def _ctx_set(origin: str, intent=None) -> None:
    """Marca a origem/intenção do fluxo em curso nesta thread; o próximo _history_append grava no ledger."""
    _CTX.origin = origin
    _CTX.intent = intent


def _thread_ctx(target, *args, **kw) -> None:
    """threading.Thread que HERDA origin/intent do fluxo que a criou (thread-local não propaga)."""
    origin = getattr(_CTX, "origin", "geral")
    intent = getattr(_CTX, "intent", None)

    def _run() -> None:
        _ctx_set(origin, intent)
        target(*args, **kw)

    threading.Thread(target=_run, daemon=True).start()


def _ledger_append(phone: str, role: str, content: str, ts: float) -> None:
    """Uma linha no ledger de compliance (append-only, nunca é apagado nem cortado)."""
    try:
        dt = datetime.fromtimestamp(ts, LEDGER_TZ)
        line = json.dumps(
            {
                "ts": round(ts, 3),
                "date": dt.strftime("%Y-%m-%d"),
                "time": dt.strftime("%H:%M:%S"),
                "datetime": dt.isoformat(),
                "phone": phone,
                "role": role,
                "intent": getattr(_CTX, "intent", None),
                "origin": getattr(_CTX, "origin", "geral"),
                "content": content,
            },
            ensure_ascii=False,
        )
        with LEDGER_LOCK:
            with open(LEDGER_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as e:
        print(f"[ledger] append falhou: {e}", flush=True)


def _history_append(phone: str, role: str, content: str, metadata: dict | None = None) -> None:
    with HISTORY_LOCK:
        ts = time.time()
        hist = HISTORY.setdefault(phone, [])
        item = {"role": role, "content": content, "ts": ts}
        if (
            isinstance(metadata, dict)
            and metadata.get("kind") == "image"
            and metadata.get("asset_id") in HUMAN_SUPPORT_ASSET_IDS
        ):
            item.update({"kind": "image", "asset_id": metadata["asset_id"]})
        hist.append(item)
        del hist[:-MAX_HISTORY]
        if len(HISTORY) > MAX_PHONES:
            oldest = min(HISTORY, key=lambda p: HISTORY[p][-1].get("ts", 0) if HISTORY[p] else 0)
            del HISTORY[oldest]
        _history_save()
        _ledger_append(phone, role, content, ts)


def _history_clear(phone: str) -> None:
    with HISTORY_LOCK:
        HISTORY.pop(phone, None)
        _history_save()
    # o ledger de compliance NÃO é tocado aqui — limpar a conversa não apaga o histórico legal


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
HAS_MACHINE_PAT = r"\bj[áa] tenho (?:uma?\s+)?m[áa]quin(?:inha|a)\b|\bminha m[áa]quina atual\b"
EXPLICIT_NO_INTEREST_PAT = r"n[ãa]o tenho interesse|sem interesse|deixa pra l[áa]|deixa quieto|dispenso|n[ãa]o quero"
INTEREST_ESCAPE_PAT = r"\bmas\b|\?|taxa|quanto|vale a pena|compar|quero saber|me conta|me fala"
# "interessado neste modelo/produto" SEM card de catálogo: confirmar, nunca afirmar modelo às cegas
INTEREST_MODEL_PAT = r"interessad[oa].{0,25}(modelo|produto)|quero (esse|este|es[sa]) (modelo|produto)"

CANNED_EXIT = ("Tranquilo, entendo! Só reforço que a Ton tem Pix 0% com chave cadastrada e sem mensalidade "
               "— se mudar de ideia, tô por aqui. Obrigado pela atenção! 🙂")

DOUBT_CHECK = "Você tem alguma dúvida até aqui? 🙂"

# "não" após a pergunta de dúvida = sem dúvidas (sinal POSITIVO), não é desinteresse.
NO_DOUBTS_PAT = (r"^(n[ãa]o|nao)[.,!]*$"
                 r"|^(n[ãa]o|nao)[, ]+(tudo (certo|bem|ok)|nenhuma|nada|sem d[úu]vida)"
                 r"|^nenhuma[.,!]*$"
                 r"|^sem d[úu]vida"
                 r"|^tudo (certo|bem|ok)[.,!]*$")
NO_DOUBTS_REPLY = "Perfeito! Se aparecer qualquer dúvida, é só me chamar. Tô por aqui. 🙂"

# Encerramento suave: sem pressa, sem link, sem oferta — só registra e se despede.
SOFT_EXIT_PAT = (r"nada por enquanto|s[óo]( estou)? (olhando|pesquisando|vendo)|agora n[ãa]o|outra hora"
                 r"|depois (eu |a gente )(fala|conversa|ve)|mais tarde|vou pensar|t[ôo] pensando"
                 r"|deixa (pra|para) depois|por enquanto (n[ãa]o|é só)|só curiosidade|apenas curiosidade")
SOFT_EXIT_REPLY = ("Tranquilo, sem pressa! 🙂 Vou deixar tudo registrado aqui. "
                   "Quando quiser retomar, é só me chamar — tô por aqui.")

# Áudios FIXOS (voz clonada) para os 3 momentos de conexão. Gravações em audios/.
AUDIOS_ARQUIVOS = {
    "boas_vindas": "Boas-vindas.mp3",
    "objecao": "Quebra de objeção.mp3",
    "fechamento": "Fechamento.mp3",
}
# Transcrições das gravações: entram no histórico (o áudio em si não é texto pra LLM).
AUDIOS_FALAS = {
    "boas_vindas": ("Fala, tudo bem? Aqui é o Tony, parceiro da Ton. Legal ver seu interesse. "
                    "Me conta, que tipo de negócio você tem?"),
    "objecao": ("Tudo bem, é tranquilo perguntar isso. É que muita gente vem com medo de taxa escondida. "
                "No app você é informado a cada venda sobre a taxa aplicada, tudo certinho, sem letra miúda. "
                "E dá pra pagar zero no Pix, com a chave da sua própria conta. "
                "Me fala uma coisa. Qual é a sua faixa de vendas por mês?"),
    "fechamento": ("Então é isso. A condição de novos clientes está valendo agora, e você aproveita pagando "
                   "bem menos por venda enquanto ela durar. Eu te ajudo a escolher a maquininha certa e já "
                   "te mando o link na hora. Só me confirma, vai usar no seu CPF ou já tem CNPJ."),
}


# ---- Botões interativos (fluxo determinístico, sem LLM) ----
# (O menu inicial com "Falar com atendente" foi aposentado: a saudação vai
# direto para BTN_TIPO. O handler de "ver_maquininhas" segue existindo para
# quem ainda tem o menu antigo na tela.)
BTN_TIPO = [("loja_fisica", "Loja física"), ("servicos_externos", "Serviços externos"), ("outro", "Outro")]
BTN_COMPROVANTE = [("impresso", "Impresso"), ("digital", "Digital")]
BTN_VOLUME = [("ate_10k", "até R$10 mil"), ("10_30k", "R$10–30 mil"), ("30k_plus", "mais de R$30 mil")]
BTN_PESSOA = [("cpf", "CPF"), ("cnpj", "CNPJ")]
BTN_CNPJ_TON = [("cnpj_tem_ton_sim", "Sim"), ("cnpj_tem_ton_nao", "Não")]
BTN_CONTINUAR_CPF = [("continuar_cpf", "Continuar pelo CPF"), ("nao_seguir", "Não seguir agora")]
BTN_FINAL = [("comprar", "Comprar no site"), ("duvidas", "Tirar dúvidas"), ("atendente", "Falar com atendente")]
BTN_MAIS_DUVIDAS = [("mais_sim", "Sim"), ("mais_nao", "Não")]

def _is_greeting(texto: str) -> bool:
    return _norm(texto).strip("!., ") in GREETING_EXACT


GREETING_LIKE_PAT = re.compile(
    r"^(?:oi|ol[aá]|opa|e a[ií]|bom dia|boa tarde|boa noite|hello|hi)"
    r"(?:[!?.,\s]*(?:tudo (?:bem|bom|certo|jo[íi]a)|como (?:vai|est[aá])"
    r"|espero que esteja bem|tudo certo|tudo bem por a[íi]))?"
    r"[!?.,\s]*$", re.IGNORECASE)


def _is_greeting_like(texto: str) -> bool:
    """Saudação pura ou só cortesias ("oi, tudo bem?") — candidata à abertura padrão."""
    return bool(GREETING_LIKE_PAT.match((texto or "").strip()))


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


FOLLOWUP_PATH = BASE / "config" / "followups.json"
FOLLOWUP_TEXT = "Oi! Conseguiu finalizar sua compra? Se travou em alguma parte, me fala que eu te ajudo. 🙂"
FOLLOWUPS = {}


def _followups_load() -> None:
    try:
        if FOLLOWUP_PATH.exists():
            data = json.loads(FOLLOWUP_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # Migra formato antigo {fone: ts} -> {fone: {"t1": ts}}
                for _k, _v in data.items():
                    FOLLOWUPS[_k] = _v if isinstance(_v, dict) else {"t1": _v}
    except Exception as e:
        print(f"[followup] load falhou: {e}", flush=True)


def _followups_save() -> None:
    try:
        tmp = FOLLOWUP_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(FOLLOWUPS, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, FOLLOWUP_PATH)
    except Exception as e:
        print(f"[followup] save falhou: {e}", flush=True)


def _configured_checkout_links(cfg: dict | None = None) -> list[str]:
    cfg = cfg if cfg is not None else _load()
    return [
        link
        for modelo in (cfg.get("modelos") or [])
        for field in ("link", "link_black")
        if (link := (modelo.get(field) or "").strip())
    ]


def _last_checkout_offer(messages: list, links: list[str]) -> dict | None:
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        content = message.get("content") or ""
        if any(link in content for link in links):
            return message
    return None


def _needs_purchase_followup(phone: str) -> bool:
    msgs = _history_get(phone)
    if not msgs:
        return False
    if _is_blocked(phone):
        return False
    q_state = _q_get(phone)
    if q_state.get("step") in {
        "handoff", "atendimento", "atendimento_humano", "escalado", "posvenda", "app_os",
    }:
        return False
    offer = _last_checkout_offer(msgs, _configured_checkout_links())
    if not offer:
        return False
    offer_index = msgs.index(offer)
    purchased = False
    latest_customer_ts = 0.0
    for index, m in enumerate(msgs):
        if m.get("role") == "user":
            latest_customer_ts = max(latest_customer_ts, m.get("ts", 0))
            if index > offer_index:
                return False
        if m.get("role") == "user" and re.search(
                r"comprei|comprado|finalizei|finalizado|j[áa] pedi|pedido feito|acabei de (comprar|pedir)|pagamento feito",
                m.get("content") or "", re.IGNORECASE):
            purchased = True
    st = FOLLOWUPS.get(phone)
    st = st if isinstance(st, dict) else ({"t1": st} if st else {})
    offer_ts = offer.get("ts", 0)
    already_sent = st.get("t1") == offer_ts
    return purchase_check_due(
        offer_ts=offer_ts,
        latest_customer_ts=latest_customer_ts,
        now_ts=time.time(),
        already_purchased=purchased,
        already_sent=already_sent,
    )


# ---- Opt-out (LGPD): cliente que não quer mais mensagens ----
BLOCKED_PATH = BASE / "config" / "blocked.json"
BLOCKED: set = set()
BLOCKED_LOCK = threading.Lock()
OPT_OUT_PAT = (r"\bparem?\b|para (de )?(me )?(mandar|enviar) (mensagem|mensagens)|n[ãa]o quero (mais |receber|(mensagem|mensagens))|"
               r"me tira(r)?( da lista| dessa lista)?|me remove|descadastr|remov.*(lista|contato)|"
               r"chega de (mensagem|mensagens)|n[ãa]o (me )?(chama|chame|procura|mande|envie) mais|\bstop\b|cancele.*((mensagem|mensagens)|contato)")
OPT_OUT_REPLY = ("Entendido, sem problema! 🙂 Removi seu contato da nossa lista e você não vai "
                 "receber mais mensagens. Se precisar um dia, é só me chamar.")


def _blocked_load() -> None:
    try:
        if BLOCKED_PATH.exists():
            data = json.loads(BLOCKED_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                with BLOCKED_LOCK:
                    BLOCKED.update(data)
    except Exception as e:
        print(f"[blocked] load falhou: {e}", flush=True)


def _blocked_save() -> None:
    try:
        with BLOCKED_LOCK:
            tmp = BLOCKED_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(sorted(BLOCKED), ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, BLOCKED_PATH)
    except Exception as e:
        print(f"[blocked] save falhou: {e}", flush=True)


def _is_blocked(phone: str) -> bool:
    with BLOCKED_LOCK:
        return phone in BLOCKED


def _block(phone: str) -> None:
    with BLOCKED_LOCK:
        BLOCKED.add(phone)
    _blocked_save()


def _followup_loop() -> None:
    while True:
        time.sleep(60)
        try:
            for phone in list(HISTORY.keys()):
                if _is_blocked(phone):
                    continue
                if _needs_purchase_followup(phone):
                    cfg = _load()
                    offer = _last_checkout_offer(
                        _history_get(phone), _configured_checkout_links(cfg)
                    )
                    if not offer:
                        continue
                    _send_reply(cfg, phone, FOLLOWUP_TEXT)
                    _ctx_set("followup")
                    _history_append(phone, "assistant", FOLLOWUP_TEXT)
                    FOLLOWUPS[phone] = {"t1": offer.get("ts", 0)}
                    _followups_save()
                    print(f"[followup] ajuda pós-oferta para {phone}", flush=True)
                    continue
        except Exception as e:
            print(f"[followup] loop falhou: {e}", flush=True)


_followups_load()
_blocked_load()
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
    # Support and post-sale outrank embedded sales language in the same message.
    ("suporte", r"minha (encomenda|entrega)|cad[eê].{0,20}(pedido|entrega)|prazo.{0,20}entrega|entrega.{0,20}(atras|demor|n[ãa]o chegou|problema)|rastre|bobina|reembolso|estorno|instalar|ativar|configurar|n[ãa]o (liga|funciona|funcionou)|travou|travando|parou de|deu (defeito|problema|erro)|quebrou|quebrado|(est[áa]|veio) com (defeito|problema)|bloquead|\berro\b|\btroca\b|\bsuporte\b"),
    ("posvenda", r"comprei|chegou|pedido|pagamento (feito|aprovado)"),
    ("reclamacao", r"reclama|insatisfeit|p[ée]ssimo|horr[íi]vel|cancelar|humano|atendente|golpe|engana"),
    ("compra", r"quero comprar|vou comprar|vou querer|vou ficar|fechado|onde compro|como compro|quero uma m[áa]quina|quero pedir|vou levar"),
    ("link", r"me manda o link|manda o link|cad[êe] o link|link de (compra|pagamento)|link pra (comprar|pagar)"),
    ("modelo", None),  # preenchido por _detect_model
    ("preco", r"quanto custa|qual ([ée] o )?(valor|pre[çc]o)|pre[çc]o da|valor da|mais barata|mais em conta|menor valor|gastar o m[íi]nimo|t[áa] caro|muito caro|achei caro"),
    ("desconto", r"desconto|cupom|promo[çc][ãa]o"),
    ("taxa", r"\btaxa[s]?\b"),
    ("garantia", r"garantia|defeito|quebr|estrag|dar defeito|der defeito|se (der|quebrar|estragar|dê)"),
    ("renda", r"renda|indica|ganhar (dinheiro|renda)"),
    ("trocar", r"j[áa] tenho|tenho uma|trocar|minha atual|segunda|outra (m[áa]quina|maquininha)|mais uma"),
    ("catalogo", r"\b(quais|que) (modelos|maquininhas|m[áa]quinas)\b|op[cç][õo]es de (maquininha|m[áa]quina)"),
    ("comparacao", r"compar|diferen[çc]a|melhor| versus |\b ou \b|\bx\b"),
]

TEMP_MAP = {"compra": "quente", "link": "quente", "modelo": "quente",
            "preco": "morno", "desconto": "morno", "taxa": "morno",
            "comparacao": "morno", "trocar": "morno", "garantia": "morno"}
SCORE_MAP = {"compra": 20, "link": 15, "modelo": 15, "preco": 10,
             "desconto": 10, "taxa": 5, "comparacao": 5, "garantia": 5}

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
    for intent, pat in INTENT_PATTERNS[:3]:
        if re.search(pat, low, re.IGNORECASE):
            return intent
    if (re.search(r"\bquanto\s+(?:é|e)\b", low, re.IGNORECASE)
            and _detect_model(low, modelos or [])
            and not re.search(r"\btaxa[s]?\b", low, re.IGNORECASE)):
        return "preco"
    for intent, pat in INTENT_PATTERNS:
        if pat and re.search(pat, low, re.IGNORECASE):
            if intent == "trocar" and re.search(r"plano", low, re.IGNORECASE):
                return "taxa"  # "trocar/mudar de plano" é dúvida de plano, não de máquina
            return intent
    if _detect_model(low, modelos or []):
        return "modelo"
    return "info"


def _detect_motivo(texto: str) -> str:
    for motivo, pat in MOTIVO_PATTERNS:
        if re.search(pat, texto or "", re.IGNORECASE):
            return motivo
    return ""


def _needs_pix(textos: list[str]) -> bool:
    preference = None
    verb = (r"(?:(?:vou|iria)\s+)?(?:precis\w*|necessit\w*|necessári\w*|"
            r"quer(?:o|ia)|uso|aceit\w*|importante|essencial)|vou\s+usar")
    for texto in textos:
        value = texto or ""
        preferences = []
        patterns = (
            (rf"\b(?:n[aã]o|nunca|dispenso)\b\s*(?:(?:é|e)\s+)?(?P<verb>{verb})\b[^,.!?;]{{0,35}}\bpix\b", False),
            (rf"(?P<verb>{verb})\b[^,.!?;]{{0,35}}\bpix\b", True),
            (rf"\bpix\b[^,.!?;]{{0,25}}\b(?:n[aã]o|nunca|dispenso)\b\s*(?:(?:é|e)\s+)?(?P<verb>{verb})\b", False),
            (rf"\bpix\b[^,.!?;]{{0,25}}(?P<verb>{verb})\b", True),
        )
        for pattern, needs_pix in patterns:
            for match in re.finditer(pattern, value, re.IGNORECASE):
                # At the same verb position a negated phrase wins over its positive
                # sub-match (e.g. "não preciso de Pix"). Later verbs still win.
                preferences.append((match.start("verb"), not needs_pix, needs_pix))
        if preferences:
            preference = max(preferences, key=lambda item: (item[0], item[1]))[2]
    return preference is True


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
    if (slots.get("comprovante") or "") == "digital":
        out = f"Pelo que você me contou, a {nome} combina com o seu uso porque o comprovante digital já resolve pra você."
    elif (slots.get("comprovante") or "") == "impresso" and mid == "t3_smart":
        out = f"Pelo que você me contou, a {nome} combina com o seu uso porque tem tela Android e comprovante impresso."
    elif (slots.get("comprovante") or "") == "impresso":
        out = f"Pelo que você me contou, a {nome} combina com o seu uso porque tem comprovante impresso."
    elif mid == "t3_smart":
        out = f"A {nome} tem tela Android e comprovante impresso."
    else:
        out = f"A {nome} é uma opção do catálogo."
    if preco:
        out += (f"\n\nA {nome} é adesão única de {preco} (catálogo 07/10/2026), "
                f"parcelável {parcelas}, sem mensalidade.")
    out += ("\n\nNos planos Mega+ e Black, a taxa parte de 0,74% no débito e no crédito à vista (confirme a sua no app)."
            "\n\n" + _recommendation_fallback(modelo, slots))
    return out


def _price_overview(cfg: dict, include_qualification: bool = True) -> str:
    """Pergunta de preço sem modelo específico: informa a faixa e conduz à microqualificação."""
    modelos = cfg.get("modelos") or []

    def _val(m):
        d = re.sub(r"[^\d,]", "", (m.get("preco") or "").replace(".", ""))
        return float(d.replace(",", ".")) if d else 0.0

    com_preco = sorted([m for m in modelos if m.get("preco")], key=_val)
    if not com_preco:
        resposta = "Deixa eu confirmar os valores atuais pra te passar certinho."
        if include_qualification:
            resposta += " Você precisa de comprovante impresso?"
        return resposta
    linhas = "\n".join(f"- {m.get('nome')}: {m.get('preco')} ({m.get('parcelas') or 'em até 12x'})"
                       for m in com_preco)
    resposta = (f"Hoje temos modelos a partir de {com_preco[0].get('preco')} (adesão única, sem mensalidade — "
                f"catálogo 07/10/2026):\n{linhas}")
    if include_qualification:
        resposta += ("\n\nVocê precisa de comprovante impresso ou o digital já resolve? "
                     "Assim eu já te indico o modelo certo.")
    return resposta


def _model_price_summary(modelo: dict) -> str:
    nome = (modelo.get("nome") or "maquininha").strip()
    preco = (modelo.get("preco") or "").strip()
    parcelas = (modelo.get("parcelas") or "").strip()
    if not preco:
        return f"Vou confirmar o preço atual da {nome} para te passar certinho."
    resumo = f"A {nome} tem adesão única de {preco} (catálogo 07/10/2026)"
    if parcelas:
        resumo += f", parcelável {parcelas}"
    return resumo + ", sem mensalidade."


def _deliver_offer(cfg: dict, from_: str, modelo: dict, slots: dict, hist: list,
                   with_audio: bool = False, lead_in: str = "") -> str | None:
    """Envia o pacote da oferta (texto + link + dúvida) e registra no histórico. Devolve o texto.

    with_audio=True (momento de fechamento): toca a gravação `fechamento` antes do texto —
    ela já pergunta CPF/CNPJ, então o DOUBT_CHECK é omitido para não empilhar duas perguntas."""
    if gate := _guard_ton_checkout(cfg, from_):
        return None
    eligible, requested, incompatibility, _needs = _checkout_model_decision(
        cfg, from_, modelo, hist=hist
    )
    latest_user = next((str(item.get("content") or "") for item in reversed(hist or [])
                        if item.get("role") == "user"), "")
    price_first = bool(requested and _detect_intent(latest_user, cfg.get("modelos") or []) == "preco")
    if not eligible:
        return _send_no_compatible_checkout(
            cfg, from_, requested, incompatibility, price_first=price_first
        )
    if eligible.get("id") != modelo.get("id"):
        preface = []
        if price_first and requested:
            preface.append(_model_price_summary(requested))
        if incompatibility:
            preface.append(incompatibility)
        if lead_in and lead_in not in preface:
            preface.append(lead_in.strip())
        lead_in = "\n\n".join(part for part in preface if part)
        modelo = eligible
    _q_set(from_, step="recomendacao", modelo=modelo.get("id") or "")
    offer_slots = dict(slots or {})
    if _needs["needs_print"] is True:
        offer_slots["comprovante"] = "impresso"
    elif _needs["needs_print"] is False:
        offer_slots["comprovante"] = "digital"
    texto = _rec_package(modelo, offer_slots)
    if lead_in:
        texto = lead_in.strip() + "\n\n" + texto
    texto = _ensure_pix_note(texto, modelo)
    selected_link = _choose_link(modelo, _q_get(from_))
    texto = _apply_model_link(texto, modelo, selected_link)
    texto = _append_model_promo_details(cfg, modelo, texto, selected_link)
    if with_audio:
        _send_voice_or_text(cfg, from_, AUDIOS_FALAS.get("fechamento", ""), mp3_key="fechamento")
    for bubble in _split_bubbles(texto):
        _send_reply(cfg, from_, bubble)
    canal = cfg.get("canal") or "baileys"
    if canal == "baileys" and modelo and not _already_offered(hist, cfg.get("modelos") or []):
        if not with_audio:
            _send_voice_or_text(cfg, from_, DOUBT_CHECK)
    falas_hist = AUDIOS_FALAS.get("fechamento") if with_audio else ""
    _history_append(from_, "assistant", ((falas_hist + " ") if falas_hist else "") + texto)
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


PROMO_MODEL_IDS = {"T1": "t1", "T2": "t2", "T3": "t3", "T3 Smart": "t3_smart"}
PROMO_MODEL_SNAPSHOT_RE = re.compile(
    r"(?im)^-\s*(?P<nome>T3 Smart|T1|T2|T3):\s*"
    r"(?P<desconto>\d+%\s+OFF);\s*"
    r"(?P<cupom>sem cupom extra|cupom 20%);\s*"
    r"preço exibido:\s*(?P<preco>R\$\s*[\d,.]+)\s*$"
)
PROMO_SNAPSHOT_DATE_RE = re.compile(r"\d{2}/\d{2}/\d{4}")
PROMO_SNAPSHOT_PRICE_RE = re.compile(
    r"R\$\s*(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{2}|\.\d{2})",
    re.IGNORECASE,
)


def _valid_promo_date(value: str) -> bool:
    date_text = (value or "").strip()
    if not PROMO_SNAPSHOT_DATE_RE.fullmatch(date_text):
        return False
    try:
        datetime.strptime(date_text, "%d/%m/%Y")
    except ValueError:
        return False
    return True


def _valid_promo_price(value: str) -> bool:
    return bool(isinstance(value, str) and PROMO_SNAPSHOT_PRICE_RE.fullmatch(value.strip()))


def _promo_snapshot(cfg: dict, modelo: dict, plan: str = "mega_plus") -> dict | None:
    if not isinstance(modelo, dict):
        return None
    plan_snapshots = modelo.get("promocao_por_plano")
    if plan == "black":
        snapshot = (
            (plan_snapshots.get("black") if isinstance(plan_snapshots, dict) else None)
            or modelo.get("promocao_black")
        )
        if not isinstance(snapshot, dict):
            return None
    else:
        snapshot = (
            plan_snapshots.get("mega_plus") if isinstance(plan_snapshots, dict)
            else None
        ) or modelo.get("promocao")
    if isinstance(snapshot, dict):
        desconto = snapshot.get("desconto")
        cupom = snapshot.get("cupom")
        preco = snapshot.get("preco_observado")
        observado_em = snapshot.get("observado_em")
        if (isinstance(desconto, str)
                and re.fullmatch(r"\d{1,3}%\s+OFF", desconto.strip(), re.IGNORECASE)
                and _valid_promo_price(preco)
                and isinstance(observado_em, str) and _valid_promo_date(observado_em)
                and "cupom" in snapshot
                and (cupom is None or (isinstance(cupom, str) and cupom.strip() == "20%"))):
            return {
                **snapshot,
                "desconto": desconto.strip(),
                "cupom": cupom.strip() if isinstance(cupom, str) else None,
                "preco_observado": preco.strip(),
                "observado_em": observado_em.strip(),
            }
    if plan == "black":
        return None

    base = str((cfg or {}).get("base_conhecimento") or "")
    date_headers = list(re.finditer(
        r"(?im)^Snapshot promocional afiliado observado em (\d{2}/\d{2}/\d{4})\b",
        base,
    ))
    model_id = (modelo or {}).get("id") or ""
    for match in PROMO_MODEL_SNAPSHOT_RE.finditer(base):
        if PROMO_MODEL_IDS.get(match.group("nome")) != model_id:
            continue
        observed = next((header for header in reversed(date_headers)
                         if header.start() < match.start()), None)
        if not observed or not _valid_promo_date(observed.group(1)):
            continue
        coupon = None if match.group("cupom").lower() == "sem cupom extra" else "20%"
        preco = match.group("preco").strip()
        if not _valid_promo_price(preco):
            continue
        return {
            "desconto": match.group("desconto"),
            "cupom": coupon,
            "preco_observado": preco,
            "observado_em": observed.group(1),
        }
    return None


def _checkout_plan(modelo: dict, link: str = "") -> str:
    black_link = ((modelo or {}).get("link_black") or "").strip()
    return "black" if link and black_link and link == black_link else "mega_plus"


def _model_promo_details(cfg: dict, modelo: dict, link: str = "") -> str:
    snapshot = _promo_snapshot(cfg, modelo, plan=_checkout_plan(modelo, link))
    nome = ((modelo or {}).get("nome") or "modelo").strip()
    if not snapshot:
        return (f"Não tenho um snapshot promocional confirmado para {nome}. "
                "Promoção, disponibilidade e preço final podem mudar; confira no carrinho.")
    coupon = "sem cupom extra" if not snapshot.get("cupom") else f"cupom {snapshot['cupom']}"
    return (
        f"Snapshot afiliado observado em {snapshot['observado_em']}: {nome} estava com "
        f"{snapshot['desconto']}, {coupon}, e preço exibido no carrinho de "
        f"{snapshot['preco_observado']}. Promoção e preço podem mudar; confirme "
        "disponibilidade e valor final no carrinho."
    )


def _model_discount_copy(cfg: dict, modelo: dict, link: str) -> str:
    return "\n\n".join((DISCOUNT_CANONICAL, _model_checkout_copy(cfg, modelo, link)))


def _model_checkout_copy(cfg: dict, modelo: dict, link: str = "") -> str:
    parts = [_model_promo_details(cfg, modelo, link)]
    if link:
        parts.append(f"Link do modelo: {link}")
    return "\n\n".join(parts)


def _append_model_promo_details(cfg: dict, modelo: dict, texto: str, link: str = "") -> str:
    details = _model_promo_details(cfg, modelo, link)
    if details in (texto or ""):
        return texto
    return (texto or "").rstrip() + "\n\n" + details


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

CADASTRO_QUESTION_PAT = r"\bcpf\b|\bcnpj\b|\bmei\b"


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
    if not re.search(r"link do app|link do aplicativo|baixar o app|baixe o app|baixar o aplicativo|baixe o aplicativo", texto, re.IGNORECASE):
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
        elif merged and len(merged[-1]) < 30 and not re.search(r"[.?!…:]$", merged[-1].strip()):
            # Parágrafo anterior é fragmento (ex: "Você") — gruda no atual.
            merged[-1] = merged[-1].rstrip() + " " + p
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
GREETING_START_PAT = r"^\s*(oi|ol[aá]|opa|e a[ií]|bom dia|boa tarde|boa noite)\b"


def _starts_with_greeting(texto: str) -> bool:
    return bool(re.match(GREETING_START_PAT, texto or "", re.IGNORECASE))


def _strip_reintro(texto: str, is_fresh: bool, cliente_cumprimentou: bool = False) -> str:
    """Remove auto-apresentação e saudações soltas quando a conversa já começou.

    cliente_cumprimentou=True mantém a saudação de volta ("Olá!") quando o cliente
    abriu a própria mensagem com saudação — mas a auto-apresentação é sempre cortada.
    """
    if is_fresh:
        return texto
    kept = [p for p in _split_sentences(texto)
            if not re.search(INTRO_PAT, p, re.IGNORECASE)
            and not (re.match(GREETING_PAT, p.strip(), re.IGNORECASE)
                     and not cliente_cumprimentou)]
    return "\n\n".join(kept).strip()


def _fallback_question(slots: dict) -> str:
    if "negocio" not in slots:
        return "Que tipo de negócio você tem?"
    neg = str(slots["negocio"]).capitalize()
    if "comprovante" not in slots:
        return f"{neg}, anotado! Seu cliente precisa de comprovante impresso ou o digital já resolve?"
    if "volume" not in slots:
        return "Qual é a sua faixa de vendas por mês?"
    return "Anotado! Quer que eu te mande o link da maquininha ideal pro seu caso?"


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
    # sem despedida: # FORMATO proíbe a mensagem terminar com "Obrigado!"/"tô por aqui"
    return f"Tô te enviando o link da {nome}."


PIX_STANDARD = "Pix 0% com chave cadastrada na Conta Ton (30 dias grátis pra novos clientes; sem chave, 0,49% por venda)."
PIX_T1_NONE = "A T1 não tem Pix na maquininha."
TAXA_SAFE_REPLY = ("As taxas de débito e crédito dependem do plano e da condição vigente da sua conta; "
                   "não vou estimar um percentual sem essa confirmação. Você pode conferir a taxa aplicada "
                   "no app Ton antes de concluir cada venda.")


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
    return f"A {last_modelo.get('nome')} é adesão única de {preco} (catálogo 07/10/2026), parcelável {parcelas}, sem mensalidade."


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


def _strip_urls(texto: str) -> str:
    return _strip_orphan_url_ctas(LLM_URL_RE.sub("", texto or ""))


LLM_URL_RE = re.compile(
    r"(?i)(?:https?://|www\.)[^\s<>\"'`]+|"
    r"(?<![@\w.-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}"
    r"(?::\d+)?(?:[/?][^\s<>\"'`]*)?"
)
ORPHAN_URL_CTA_RE = re.compile(
    r"(?im)(?:^\s*|(?<=[.!?])\s*)"
    r"(?:consulte(?: também)?|veja(?: aqui)?|acesse|clique(?: aqui)?|confira|"
    r"checkout|link(?:\s+do)?\s+checkout)\s*:?[ \t]*(?=$|[.!?])"
)
LLM_NUMBER_WORD = (
    r"zero|um|uma|dois|duas|tr[eê]s|quatro|cinco|seis|sete|oito|nove|dez|"
    r"onze|doze|treze|catorze|quatorze|quinze|dezesseis|dezessete|dezoito|dezenove|"
    r"vinte|trinta|quarenta|cinquenta|sessenta|setenta|oitenta|noventa|cem|cento|"
    r"duzentos|duzentas|trezentos|trezentas|quatrocentos|quatrocentas|"
    r"quinhentos|quinhentas|seiscentos|seiscentas|setecentos|setecentas|"
    r"oitocentos|oitocentas|novecentos|novecentas|mil|milh(?:ão|ões)"
)
LLM_WRITTEN_CURRENCY_RE = re.compile(
    rf"\b(?:{LLM_NUMBER_WORD})(?:[\s-]+(?:e[\s-]+)?(?:{LLM_NUMBER_WORD})){{0,5}}\s+reais?\b",
    re.IGNORECASE,
)
LLM_COMMERCIAL_CLAIM_RE = re.compile(
    r"(?:\bR\$\s*\d|\b\d+(?:[,.]\d+)?\s*%|"
    r"\b\d+(?:[,.]\d+)?\s+reais?\b|"
    + LLM_WRITTEN_CURRENCY_RE.pattern
    + r"|\bcupom\b|\bdescont\w*|"
    r"\bpromo\w*|\bcashback\b|\bpre[cç]o\b|\bcusta\w*|\bcusto\b|"
    r"\bparcel\w*|\b\d+\s*(?:x|vezes?)\b)",
    re.IGNORECASE,
)


def _strip_unverified_commercial_claims(texto: str) -> str:
    kept = []
    for sentence in _split_sentences(texto or ""):
        for clause in re.split(r"(?<=;)\s*", sentence):
            clause = clause.strip()
            if not clause:
                continue
            if clause == DISCOUNT_CANONICAL:
                kept.append(clause)
            elif not LLM_COMMERCIAL_CLAIM_RE.search(clause):
                kept.append(clause.rstrip(";"))
    return "\n\n".join(kept).strip()


SUPPORT_PURCHASE_PITCH_RE = re.compile(
    r"\b(?:sugiro|recomendo|aconselho|indico)\b.{0,60}\b(?:comprar|adquirir|levar)\b"
    r"|\baproveite\b.{0,70}\b(?:oferta|promo[çc][ãa]o|desconto|cupom)\b"
    r"(?:.{0,50}\b(?:compre|comprar|adquira|leve|garanta)\b)?"
    r"|\bgaranta\b.{0,50}\b(?:sua|seu|uma|um)?\s*"
    r"(?:t[123](?:\s?smart)?|maquininha|oferta|promo[çc][ãa]o|desconto|cupom|compra|checkout|link)\b"
    r"|\b(?:compre|adquira|leve)\b.{0,50}"
    r"\b(?:t[123](?:\s?smart)?|maquininha|oferta|promo[çc][ãa]o|desconto|checkout|link|agora)\b"
    r"|\b(?:finalize|conclua|feche)\b.{0,40}\b(?:a\s+)?(?:compra|pedido|checkout)\b"
    r"|\b(?:acesse|clique|abra)\b.{0,40}\b(?:o\s+)?(?:checkout|link de compra)\b"
    r"|\b(?:v[aá]|vai)\s+(?:ao|para o)\s+checkout\b",
    re.IGNORECASE,
)
SUPPORT_PURCHASE_NEGATION_RE = re.compile(
    r"(?:\b(?:n[aã]o|nunca|jamais)\s+(?:vai|v[aá])\s+(?:ao|para o)\s+checkout\b|"
    r"\b(?:n[aã]o|nunca|jamais)\b[^.!?]{0,60}"
    r"\b(?:compre|comprar|adquira|leve|aproveite|garanta|finalize|conclua|feche)\b|"
    r"\b(?:n[aã]o|nunca|jamais)\b[^.!?]{0,50}\b(?:sugiro|recomendo|aconselho|indico)\b|"
    r"\b(?:sugiro|recomendo|aconselho|indico)\b[^.!?]{0,40}"
    r"\b(?:n[aã]o|nunca|jamais)\b[^.!?]{0,20}\b(?:comprar|adquirir|levar)\b)",
    re.IGNORECASE,
)
SUPPORT_CONTRAST_CLAUSE_RE = re.compile(
    r"\s*(?:;\s*|,\s*|,?\s+\b(?:mas|porém|porem|contudo|entretanto|todavia)\b\s+)",
    re.IGNORECASE,
)


def _strip_support_sales_pitch(texto: str) -> str:
    kept = []
    for sentence in _split_sentences(texto or ""):
        clauses = []
        for clause in SUPPORT_CONTRAST_CLAUSE_RE.split(sentence):
            joined = re.split(r"\s+\be\b\s+", clause, flags=re.IGNORECASE)
            has_positive_pitch = (
                SUPPORT_PURCHASE_PITCH_RE.search(clause)
                and not SUPPORT_PURCHASE_NEGATION_RE.search(clause)
            )
            has_later_positive_pitch = any(
                SUPPORT_PURCHASE_PITCH_RE.search(part)
                and not SUPPORT_PURCHASE_NEGATION_RE.search(part)
                for part in joined[1:]
            )
            if len(joined) > 1 and (has_positive_pitch or has_later_positive_pitch):
                clauses.extend(joined)
                continue
            clauses.append(clause)

        positive_pitch_indexes = {
            index for index, clause in enumerate(clauses)
            if (SUPPORT_PURCHASE_PITCH_RE.search(clause)
                and not SUPPORT_PURCHASE_NEGATION_RE.search(clause))
        }
        if not positive_pitch_indexes:
            kept.append(sentence)
            continue

        kept_clauses = []
        for index, clause in enumerate(clauses):
            if index in positive_pitch_indexes:
                continue
            clause = clause.strip(" \t\r\n,;:")
            if clause:
                if clause[-1] not in ".?!…":
                    clause += "."
                clause = clause[0].upper() + clause[1:]
                kept_clauses.append(clause)
        if kept_clauses:
            kept.append(" ".join(kept_clauses))
    return "\n\n".join(kept).strip()


def _configured_url_allowlist(cfg: dict) -> set[str]:
    allowed = set()
    configured_checkouts = {
        (modelo.get(field) or "").strip()
        for modelo in (cfg or {}).get("modelos") or []
        for field in ("link", "link_black")
        if (modelo.get(field) or "").strip()
    }
    for field in ("link_app", "link_app_ios", "link_app_android"):
        link = ((cfg or {}).get(field) or "").strip()
        if link:
            allowed.add(link)
    base = str((cfg or {}).get("base_conhecimento") or "")
    allowed.update(match.group(0).rstrip(".,!?;:)]}") for match in LLM_URL_RE.finditer(base))
    allowed.difference_update(configured_checkouts)
    return allowed


def _sanitize_llm_urls(texto: str, cfg: dict) -> str:
    allowed = _configured_url_allowlist(cfg)

    def replace(match):
        token = match.group(0)
        url = token.rstrip(".,!?;:)]}")
        punctuation = token[len(url):]
        return token if url in allowed else punctuation

    sanitized = _strip_orphan_url_ctas(LLM_URL_RE.sub(replace, texto or ""))
    return re.sub(r"\n{3,}", "\n\n", sanitized).strip()


def _strip_orphan_url_ctas(texto: str) -> str:
    return re.sub(ORPHAN_URL_CTA_RE, "", texto or "").strip()


def _apply_model_link(texto: str, modelo: dict, link: str | None = None) -> str:
    """Remove URLs supplied in free text and attach only the selected configured URL."""
    short = (link if link is not None else (modelo or {}).get("link") or "").strip()
    texto = _strip_urls(texto)
    if short:
        texto = (texto + "\n\n" + short).strip() if texto else short
    return texto


def _contains_checkout_link(texto: str, cfg: dict) -> bool:
    text = texto or ""
    configured = [
        (modelo.get(campo) or "").strip()
        for modelo in (cfg.get("modelos") or [])
        for campo in ("link", "link_black")
    ]
    return (any(link and link in text for link in configured)
            or bool(re.search(r"https?://(?:www\.)?ton\.com\.br/checkout\b", text, re.IGNORECASE)))


def _catalog_reply(modelos: list, user_text: str) -> str:
    asked_tapton = bool(re.search(r"\btap\s?ton\b", user_text or "", re.IGNORECASE))
    available = [
        (modelo.get("nome") or "").strip()
        for modelo in (modelos or [])
        if (modelo.get("nome") or "").strip()
        and (asked_tapton or not re.search(
            r"tap\s?ton", " ".join((modelo.get("id") or "", modelo.get("nome") or "")),
            re.IGNORECASE,
        ))
    ]
    if not available:
        return "Vou confirmar o catálogo atual para te informar os modelos disponíveis certinho."
    return "No catálogo disponível, temos: " + ", ".join(available) + "."


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


def _register_business_event(cfg: dict, conversation_id: str, event_type: str = "lead",
                             event_value=None, waba_id: str = "") -> dict:
    """Registra um business event na conta WhatsApp Business (permissão
    whatsapp_business_manage_events). Não-fatal: nunca interrompe o fluxo."""
    meta = cfg.get("meta") or {}
    be = meta.get("business_events") or {}
    if not be.get("enabled"):
        return {"skipped": "disabled"}
    version = meta.get("version") or "v25.0"
    token = meta.get("access_token") or ""
    waba = waba_id or meta.get("waba_id") or ""
    if not waba:
        return {"skipped": "no_waba_id"}
    if not conversation_id:
        return {"skipped": "no_conversation_id"}
    ev = {
        "event_type": be.get("event_type") or event_type,
        "event_timestamp": int(time.time()),
    }
    if event_value is not None:
        ev["event_value"] = event_value
    if be.get("currency"):
        ev["event_metadata"] = {"currency": be["currency"]}
    try:
        return _http_json(
            f"https://graph.facebook.com/{version}/{waba}/business_events",
            {"Authorization": "Bearer " + token},
            {"conversation_id": conversation_id, "business_events": [ev]},
        )
    except Exception as e:
        return {"error": str(e)}


# ---- Autenticação (login comum + Facebook Login) ----
def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(USERS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return base64.b64encode(salt + dk).decode("ascii")


def _verify_password(password: str, stored: str) -> bool:
    try:
        raw = base64.b64decode(stored)
        salt, dk = raw[:16], raw[16:]
        check = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
        return hmac.compare_digest(check, dk)
    except Exception:
        return False


def _seed_admin():
    conn = _db()
    row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    if row["n"] == 0:
        auth_cfg = _load().get("auth") or {}
        email = (auth_cfg.get("admin_email") or os.environ.get("ADMIN_EMAIL")
                 or "acosta.apk@gmail.com").strip().lower()
        senha = (auth_cfg.get("admin_password") or os.environ.get("ADMIN_PASSWORD")
                 or secrets.token_urlsafe(9))
        conn.execute(
            "INSERT INTO users (email, password_hash, name, provider, created_at) "
            "VALUES (?,?,?,?,?)",
            (email, _hash_password(senha), "Admin", "email", int(time.time())),
        )
        conn.commit()
        print(f"[auth] admin criado: {email} / senha: {senha}", flush=True)
    conn.close()


def _init_db():
    conn = _db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE,
            password_hash TEXT,
            name TEXT,
            provider TEXT DEFAULT 'email',
            facebook_id TEXT UNIQUE,
            picture_url TEXT,
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at INTEGER,
            expires_at INTEGER
        );
        """
    )
    conn.commit()
    conn.close()
    _seed_admin()


def _create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    conn = _db()
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
        (token, user_id, now, now + SESSION_TTL),
    )
    conn.commit()
    conn.close()
    return token


def _current_user(request: Request):
    token = request.cookies.get("session_token") or ""
    if not token:
        return None
    conn = _db()
    row = conn.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token = ? AND s.expires_at > ?",
        (token, int(time.time())),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _delete_session(request: Request):
    token = request.cookies.get("session_token") or ""
    if token:
        conn = _db()
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
        conn.close()


def _set_session_cookie(resp, token: str):
    resp.set_cookie("session_token", token, httponly=True, samesite="lax", max_age=SESSION_TTL)


# Marcador de áudio do LLM: [ÁUDIO: objecao] (chave -> gravação fixa, preferido) ou
# [ÁUDIO: "fala conversacional"] (fala livre -> TTS, fallback legado).
AUDIO_PAT = re.compile(r'\[\s*[ÁA]UDIO\s*:\s*"([^"]{2,600})"\s*\]')
AUDIO_PAT_BARE = re.compile(r"\[\s*[ÁA]UDIO\s*:\s*([^\]]{2,600})\]")


def _audio_key(bruto: str) -> str:
    """Normaliza o conteúdo do marcador para a chave do áudio fixo ('' se não for chave)."""
    k = unicodedata.normalize("NFKD", (bruto or "").strip().lower())
    k = "".join(c for c in k if not unicodedata.combining(c))
    k = re.sub(r"[^a-z]+", "_", k).strip("_")
    if k in ("boas_vindas", "boasvindas", "saudacao", "inicio"):
        return "boas_vindas"
    if k in ("objecao", "quebra_de_objecao", "objecoes", "quebra_objecao"):
        return "objecao"
    if k in ("fechamento", "fechar", "close"):
        return "fechamento"
    return ""


def _split_audio_marker(texto: str) -> tuple:
    """Separa o marcador de áudio do restante.
    Retorna (chave_audio, fala_tts, texto_sem_marcador):
      - chave preenchida  -> usa a gravação fixa do momento (fala_tts vazio);
      - fala preenchida   -> TTS legado (fala livre dentro de aspas);
      - ambos vazios      -> não havia marcador."""
    t = (texto or "").strip()
    # vence o marcador mais à esquerda, não o padrão com aspas — assim uma chave nunca
    # é ignorada por causa de uma fala livre posterior.
    m = None
    for pat in (AUDIO_PAT, AUDIO_PAT_BARE):
        mm = pat.search(t)
        if mm and (m is None or mm.start() < m.start()):
            m = mm
    if not m:
        return "", "", t
    conteudo = m.group(1).strip().strip('"').strip()
    resto = (t[:m.start()] + " " + t[m.end():]).strip()
    # duplicatas do marcador nunca chegam ao cliente em texto cru
    resto = AUDIO_PAT.sub(" ", AUDIO_PAT_BARE.sub(" ", resto))
    resto = re.sub(r"[ \t]{2,}", " ", resto)
    resto = re.sub(r"\n{3,}", "\n\n", resto).strip(" \n-–•")
    if not conteudo:
        return "", "", resto
    chave = _audio_key(conteudo)
    if chave:
        return chave, "", resto
    return "", conteudo, resto


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
        "{{promocao}}": (cfg.get("promocao") or "").strip(),
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


def _extract_quoted(body) -> str:
    """Contexto de mensagem citada/resposta (ex.: produto do catálogo que o cliente consultou).

    Evolution API (Baileys): data.message.contextInfo.quotedMessage.
    Meta Cloud API não devolve o conteúdo citado (só o id) — retorna "".
    """
    try:
        if body.get("event") == "messages.upsert":
            msg = (body.get("data") or {}).get("message") or {}
            ctx = msg.get("contextInfo") or {}
            quoted = ctx.get("quotedMessage") or {}
            prod = ((quoted.get("productMessage") or {}).get("product")) or {}
            title = (prod.get("title") or "").strip()
            if title:
                return f'cliente citou no catálogo o produto: "{title}"'
            ext = quoted.get("extendedTextMessage") or {}
            qt = (ext.get("text") or "").strip() or (quoted.get("conversation") or "").strip()
            if qt:
                return f'cliente citou a mensagem: "{qt[:200]}"'
    except Exception:
        return ""
    return ""


def _send_reply(cfg: dict, from_: str, resposta: str) -> None:
    if _is_blocked(from_):
        print(f"[blocked] envio suprimido para {from_}", flush=True)
        return
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


def _send_buttons(cfg: dict, from_: str, texto: str, buttons: list) -> None:
    """Envia mensagem interativa com botões de resposta (Meta interactive.button / Baileys sendButtons)."""
    if _is_blocked(from_):
        print(f"[blocked] botões suprimidos para {from_}", flush=True)
        return
    canal = cfg.get("canal") or "baileys"
    if canal == "meta":
        meta = cfg.get("meta") or {}
        version = meta.get("version") or "v25.0"
        phone = meta.get("phone_number_id") or ""
        token = meta.get("access_token") or ""
        _http_json(
            f"https://graph.facebook.com/{version}/{phone}/messages",
            {"Authorization": "Bearer " + token},
            {
                "messaging_product": "whatsapp",
                "to": from_,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": texto},
                    "action": {"buttons": [
                        {"type": "reply", "reply": {"id": bid, "title": title}}
                        for bid, title in buttons
                    ]},
                },
            },
        )
    else:  # baileys / Evolution API
        evo = cfg.get("evolution") or {}
        api_url = evo.get("api_url") or "http://localhost:8080"
        api_key = evo.get("api_key") or ""
        instance = evo.get("instance") or "toni"
        _http_json(
            f"{api_url}/message/sendButtons/{instance}",
            {"apikey": api_key},
            {
                "number": from_,
                "title": texto,
                "description": "",
                "footer": "",
                "buttons": [{"type": "reply", "displayText": title, "id": bid} for bid, title in buttons],
            },
        )


def _extract_button_id(body) -> str:
    """Id do botão clicado (Meta interactive ou Baileys buttonsResponse), ou ''."""
    try:
        if body.get("object") == "whatsapp_business_account":
            value = (((body.get("entry") or [{}])[0].get("changes") or [{}])[0]).get("value") or {}
            msgs = value.get("messages") or []
            if msgs and msgs[0].get("type") == "interactive":
                inter = msgs[0].get("interactive") or {}
                return ((inter.get("button_reply") or {}).get("id")
                        or (inter.get("list_reply") or {}).get("id") or "")
        if body.get("event") == "messages.upsert":
            msg = (body.get("data") or {}).get("message") or {}
            return (msg.get("buttonsResponseMessage") or {}).get("selectedButtonId") or ""
    except Exception:
        return ""
    return ""


# ---- Estado da qualificação por botões (determinístico, sem LLM) ----
QUALIFY_PATH = BASE / "config" / "qualify.json"
QUALIFY = {}
QUALIFY_LOCK = threading.Lock()


def _qualify_load() -> None:
    try:
        if QUALIFY_PATH.exists():
            data = json.loads(QUALIFY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                QUALIFY.update(data)
    except Exception as e:
        print(f"[qualify] load falhou: {e}", flush=True)


def _qualify_save() -> None:
    try:
        tmp = QUALIFY_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(QUALIFY, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, QUALIFY_PATH)
    except Exception as e:
        print(f"[qualify] save falhou: {e}", flush=True)


def _q_get(phone: str) -> dict:
    with QUALIFY_LOCK:
        return dict(QUALIFY.get(phone) or {})


def _q_set(phone: str, **kw) -> None:
    with QUALIFY_LOCK:
        QUALIFY.setdefault(phone, {}).update(kw)
    _qualify_save()


def _q_clear(phone: str) -> None:
    with QUALIFY_LOCK:
        QUALIFY.pop(phone, None)
    _qualify_save()


# Passos em que o bot fica em silêncio: conversa com humano (handoff do bot
# ou atendimento assumido pelo painel). Só sai com recomeço ("esquecer").
HANDOFF_SILENCE_STEPS = frozenset({"handoff", "escalado"})


def _is_uncovered_handoff_reply(texto: str) -> bool:
    """Resposta terminal de encaminhamento por dúvida fora da base."""
    return bool(re.search(
        r"vou te encaminhar (pra|para) (nossa equipe|atendimento humano)"
        r"|vou encaminhar seu caso para atendimento humano",
        texto or "",
        re.IGNORECASE,
    ))


def _escalar(cfg: dict, from_: str):
    txt = "Perfeito! Vou te encaminhar pra nossa equipe, que vai te ajudar melhor com isso. 🙂"
    _history_append(from_, "assistant", txt)
    _send_reply(cfg, from_, txt)
    _q_set(from_, step="handoff")
    _log_event("escalonamento", from_, "")
    return {"ok": True, "status": "escalado"}


def _choose_link(modelo: dict, q: dict) -> str:
    """Link de checkout do modelo: Ton Black (link_black) p/ CNPJ com faturamento alto."""
    if q.get("pessoa") == "cnpj" and q.get("volume") == "30k_plus":
        black = ((modelo or {}).get("link_black") or "").strip()
        if black:
            return black
    return ((modelo or {}).get("link") or "").strip()


def _selected_model(modelos: list, q: dict, hist: list) -> dict | None:
    by_id = {m.get("id"): m for m in (modelos or []) if m.get("id")}
    if q.get("modelo") in by_id:
        return by_id[q["modelo"]]
    for item in reversed(hist or []):
        if item.get("role") == "assistant":
            found = _detect_model(item.get("content") or "", modelos or [])
            if found:
                return found
    return None


def _checkout_model_decision(
    cfg: dict,
    from_: str,
    requested: dict | None = None,
    *,
    q: dict | None = None,
    hist: list | None = None,
    modelos: list | None = None,
    respect_history_model: bool = True,
) -> tuple[dict | None, dict | None, str, dict]:
    """Resolve a requested/stored model against the customer's current stated needs."""
    modelos = modelos if modelos is not None else (cfg.get("modelos") or [])
    q = q if q is not None else _q_get(from_)
    hist = hist if hist is not None else _history_get(from_)
    user_texts = [str(item.get("content") or "") for item in hist
                  if item.get("role") == "user"]
    if requested is None and respect_history_model:
        requested = _detect_model(" ".join(user_texts), modelos) or _selected_model(
            modelos, q, hist
        )

    needs_print = (
        True if q.get("comprovante") == "impresso"
        else False if q.get("comprovante") == "digital"
        else None
    )
    print_preference = None
    print_patterns = (
        (r"\[botão\]\s*comprovante=digital|comprovante\s+(?:é\s+)?digital|"
         r"digital\s+(?:já\s+)?resolve|não\s+(?:preciso|quero|vou precisar)\s+(?:de\s+)?"
         r"(?:imprimir|comprovante\s+impresso)", False),
        (r"\[botão\]\s*comprovante=impresso|\bimpresso\b|\bimprimir\b|"
         r"\bimpressora\b|\bimpressão\b", True),
    )
    for text in user_texts:
        false_matches = [
            match for pattern, value in print_patterns if value is False
            for match in re.finditer(pattern, text, re.IGNORECASE)
        ]
        matches = [(match.start(), False) for match in false_matches]
        for pattern, value in print_patterns:
            if value is not True:
                continue
            for match in re.finditer(pattern, text, re.IGNORECASE):
                if any(negative.start() <= match.start() and negative.end() >= match.end()
                       for negative in false_matches):
                    continue
                matches.append((match.start(), True))
        if matches:
            print_preference = max(matches, key=lambda match: match[0])[1]
    if print_preference is not None:
        needs_print = print_preference

    blob = " ".join(user_texts)
    touchscreen_need = bool(re.search(
        r"\b(?:precis\w*|necessit\w*|quer(?:o|ia)?|tem que ter|tenha|uso|"
        r"importante|essencial)\b[^.!?;]{0,50}\b(?:tela|touch(?:screen)?|4g)\b"
        r"|\b(?:tela|touch(?:screen)?|4g)\b[^.!?;]{0,35}\b(?:é|são|for|"
        r"importante|essencial|preciso|necessário)\b",
        blob,
        re.IGNORECASE,
    ))
    needs = {
        "needs_print": needs_print,
        "needs_pix": _needs_pix(user_texts),
        "wants_touchscreen_or_4g": touchscreen_need or bool(
            q.get("needs_touchscreen") or q.get("needs_4g")
        ),
        "lowest_investment": bool(re.search(
            r"gastar o m[íi]nimo|menor (investimento|valor|pre[çc]o)|mais barat|"
            r"economiz|entrada m[íi]nima|investir pouco|sem gastar muito",
            blob,
            re.IGNORECASE,
        )),
    }
    requested_id = (requested or {}).get("id")
    catalog_ids = {model.get("id") for model in modelos if model.get("id")}
    selected_id = recommend_model(
        explicit_model=requested_id,
        needs_print=needs["needs_print"],
        wants_touchscreen_or_4g=needs["wants_touchscreen_or_4g"],
        needs_pix=needs["needs_pix"],
        lowest_investment=needs["lowest_investment"],
        catalog_ids=catalog_ids,
    )
    by_id = {model.get("id"): model for model in modelos if model.get("id")}
    selected = by_id.get(selected_id) if selected_id else None

    explanation = ""
    if requested and requested.get("id") != selected_id:
        model_id = requested.get("id")
        requested_name = (requested.get("nome") or model_id or "modelo").strip()
        issues = []
        if needs["needs_print"] and model_id not in {"t3", "t3_smart"}:
            issues.append(f"a {requested_name} não imprime comprovante")
        if needs["needs_pix"] and model_id == "t1":
            issues.append("a T1 não tem Pix na maquininha")
        if needs["wants_touchscreen_or_4g"] and model_id != "t3_smart":
            issues.append(f"a {requested_name} não atende à necessidade de tela Android ou 4G")
        if issues:
            mismatch = "; ".join(issues)
            if selected:
                selected_name = (selected.get("nome") or selected_id or "modelo").strip()
                if model_id == "t1" and needs["needs_pix"]:
                    limitations = [PIX_T1_NONE]
                    if needs["needs_print"]:
                        limitations.append("A T1 não imprime comprovante.")
                    if needs["wants_touchscreen_or_4g"]:
                        limitations.append("A T1 não atende à necessidade de tela Android ou 4G.")
                    limitations.append(
                        f"Como você precisa de Pix, a {selected_name} é a alternativa "
                        "compatível disponível no catálogo."
                    )
                    explanation = " ".join(limitations)
                else:
                    explanation = (
                        f"Você mencionou a {requested_name}, mas {mismatch}; "
                        f"a {selected_name} atende às necessidades informadas e é a alternativa "
                        "compatível disponível no catálogo."
                    )
            else:
                explanation = mismatch[0].upper() + mismatch[1:] + "."
    return selected, requested, explanation, needs


def _no_compatible_checkout_text(requested: dict | None, explanation: str = "") -> str:
    parts = [explanation] if explanation else []
    parts.append(
        "Não encontrei uma opção compatível no catálogo atual. "
        "Posso encaminhar sua dúvida para atendimento humano."
    )
    return "\n\n".join(parts)


def _send_no_compatible_checkout(
    cfg: dict, from_: str, requested: dict | None = None, explanation: str = "",
    *, price_first: bool = False, prefix: str = "",
) -> str:
    parts = []
    if prefix:
        parts.append(prefix.strip())
    if price_first and requested:
        parts.append(_model_price_summary(requested))
    text = _no_compatible_checkout_text(requested, explanation)
    parts.append(text)
    response = "\n\n".join(parts)
    _history_append(from_, "assistant", response)
    _send_reply(cfg, from_, response)
    _send_buttons(cfg, from_, "Como quer seguir?", [("atendente", "Falar com atendente")])
    _q_set(from_, step="atendimento", modelo="")
    _log_event("modelo_indisponivel", from_, "catalogo_sem_modelo_compativel")
    return response


def _ton_checkout_gate_kind(q: dict) -> str | None:
    if q.get("pessoa") != "cnpj" or q.get("continue_as_cpf") is True:
        return None
    if same_cnpj_ton_restricted(
        selected_document=q.get("pessoa") or "",
        has_active_ton=q.get("has_active_ton") is True,
    ):
        return "active"
    if q.get("has_active_ton") is not False or q.get("step") == "cnpj_ton":
        return "pending"
    return None


def _guard_ton_checkout(cfg: dict, from_: str):
    """Require a resolved inactive-Ton state or an explicit CPF continuation for CNPJ."""
    q = _q_get(from_)
    gate_kind = _ton_checkout_gate_kind(q)
    if gate_kind == "active":
        texto = "Você prefere continuar pelo CPF ou não seguir agora?"
        _history_append(from_, "assistant", texto)
        _send_buttons(cfg, from_, texto, BTN_CONTINUAR_CPF)
        _q_set(from_, step="continuar_cpf")
        return {"ok": True, "status": "btn_continuar_cpf"}

    if gate_kind == "pending":
        texto = "Você já tem Ton ativa neste CNPJ?"
        _history_append(from_, "assistant", texto)
        _send_buttons(cfg, from_, texto, BTN_CNPJ_TON)
        _q_set(from_, step="cnpj_ton")
        return {"ok": True, "status": "btn_cnpj_ton"}

    return None


def _recommend_from_buttons(cfg: dict, from_: str, q: dict, modelos: list):
    """Escolhe e envia a recomendação após as respostas de qualificação por botões."""
    if gate := _guard_ton_checkout(cfg, from_):
        return gate

    history = _history_get(from_)
    user_texts = [m["content"] for m in history if m["role"] == "user"]
    blob = " ".join(user_texts)
    explicit = _detect_model(blob, modelos)
    modelo, _requested, incompatibility, needs = _checkout_model_decision(
        cfg, from_, explicit, q=q, hist=history, modelos=modelos
    )
    model_id = modelo.get("id") if modelo else None
    needs_pix = needs["needs_pix"]
    if not modelo:
        texto = (
            "Não encontrei um modelo que eu possa confirmar para as necessidades informadas "
            "no catálogo atual. Posso encaminhar sua dúvida para atendimento humano."
        )
        _history_append(from_, "assistant", texto)
        _send_reply(cfg, from_, texto)
        _send_buttons(cfg, from_, "Como quer seguir?", [("atendente", "Falar com atendente")])
        _q_set(from_, step="atendimento")
        _log_event("modelo_indisponivel", from_, "catalogo_sem_modelo_compativel")
        return {"ok": True, "status": "btn_sem_modelo"}

    texto = _ensure_pix_note(
        _rec_package(modelo, {"comprovante": q.get("comprovante"), "tipo_negocio": q.get("tipo")}),
        modelo,
    )
    if incompatibility:
        texto = incompatibility + "\n\n" + texto
    selected_link = _choose_link(modelo, q)
    texto = _append_model_promo_details(cfg, modelo, texto, selected_link)
    _history_append(from_, "assistant", texto)
    for bubble in _split_bubbles(texto):
        _send_reply(cfg, from_, bubble)
    _send_buttons(cfg, from_, "Como quer seguir?", BTN_FINAL)
    _q_set(from_, step="recomendacao", modelo=modelo.get("id"))
    _log_event("recomendacao", from_, modelo.get("id"))
    return {"ok": True, "status": "btn_recomendacao", "modelo": modelo.get("id")}


def _handle_button(cfg: dict, from_: str, button_id: str):
    """Fluxo determinístico dos botões — não chama o LLM."""
    _ctx_set("botao")
    modelos = cfg.get("modelos") or []
    q = _q_get(from_)

    # Silêncio pós-handoff também para botões (menu antigo ainda na tela do cliente).
    if q.get("step") in HANDOFF_SILENCE_STEPS:
        _history_append(from_, "user", f"[botão] {button_id} (ignorado: handoff)")
        print(f"[handoff-silence] botao sem resposta para {from_}", flush=True)
        return {"ok": True, "status": "silenced_handoff"}

    if button_id == "ver_maquininhas":
        _history_append(from_, "assistant", "Qual o seu tipo de negócio?")
        _send_buttons(cfg, from_, "Qual o seu tipo de negócio?", BTN_TIPO)
        _q_set(from_, step="tipo")
        return {"ok": True, "status": "btn_tipo"}

    if button_id == "atendente":
        # Sem escalada automática: pede a dúvida; handoff só para dúvida fora da base.
        texto = ("Pode me contar sua dúvida? Se ela não estiver na minha base, "
                 "te encaminho para nossa equipe.")
        _history_append(from_, "assistant", texto)
        _q_set(from_, step="duvidas")
        _send_reply(cfg, from_, texto)
        return {"ok": True, "status": "atendente_duvida"}

    if button_id in ("loja_fisica", "servicos_externos", "outro"):
        _history_append(from_, "user", f"[botão] tipo={button_id}")
        _history_append(from_, "assistant", "Comprovante impresso ou digital resolve?")
        _send_buttons(cfg, from_, "Seu cliente precisa de comprovante impresso ou o digital já resolve?", BTN_COMPROVANTE)
        _q_set(from_, step="comprovante", tipo=button_id)
        return {"ok": True, "status": "btn_comprovante"}

    if button_id in ("impresso", "digital"):
        _history_append(from_, "user", f"[botão] comprovante={button_id}")
        _history_append(from_, "assistant", "Qual a faixa de vendas mensal?")
        _send_buttons(cfg, from_, "Qual a sua faixa de vendas por mês?", BTN_VOLUME)
        _q_set(from_, step="volume", comprovante=button_id)
        return {"ok": True, "status": "btn_volume"}

    if button_id in ("ate_10k", "10_30k", "30k_plus"):
        _history_append(from_, "user", f"[botão] volume={button_id}")
        _history_append(from_, "assistant", "Você vai usar no CPF ou no CNPJ?")
        _send_buttons(cfg, from_, "A compra vai ser no CPF ou no CNPJ?", BTN_PESSOA)
        _q_set(from_, step="pessoa", volume=button_id)
        return {"ok": True, "status": "btn_pessoa"}

    if button_id in ("cpf", "cnpj"):
        _history_append(from_, "user", f"[botão] pessoa={button_id}")
        if button_id == "cnpj":
            _q_set(
                from_, step="cnpj_ton", pessoa="cnpj",
                has_active_ton=False, continue_as_cpf=False,
            )
            return _guard_ton_checkout(cfg, from_)
        _q_set(from_, pessoa="cpf", has_active_ton=False, continue_as_cpf=False)
        return _recommend_from_buttons(cfg, from_, _q_get(from_), modelos)

    if button_id in ("cnpj_tem_ton_sim", "cnpj_tem_ton_nao") and q.get("step") == "cnpj_ton":
        has_active_ton = button_id == "cnpj_tem_ton_sim"
        _history_append(from_, "user", f"[botão] ton_ativa={'sim' if has_active_ton else 'não'}")
        _q_set(
            from_, has_active_ton=has_active_ton, continue_as_cpf=False,
            step="cnpj_ton" if has_active_ton else "recomendacao",
        )
        if has_active_ton:
            return _guard_ton_checkout(cfg, from_)
        return _recommend_from_buttons(cfg, from_, _q_get(from_), modelos)

    if button_id == "continuar_cpf" and q.get("step") == "continuar_cpf" and q.get("has_active_ton") is True:
        _history_append(from_, "user", "[botão] continuar pelo CPF")
        _q_set(from_, pessoa="cpf", continue_as_cpf=True)
        return _recommend_from_buttons(cfg, from_, _q_get(from_), modelos)

    if button_id == "nao_seguir" and q.get("step") == "continuar_cpf" and q.get("has_active_ton") is True:
        _history_append(from_, "user", "[botão] não seguir agora")
        texto = "Tudo bem! Se quiser retomar depois, é só me chamar. 🙂"
        _history_append(from_, "assistant", texto)
        _send_reply(cfg, from_, texto)
        _q_set(from_, step="encerrado", continue_as_cpf=False)
        return {"ok": True, "status": "btn_nao_seguir"}

    if button_id == "comprar":
        if gate := _guard_ton_checkout(cfg, from_):
            return gate
        requested = next((m for m in modelos if m.get("id") == q.get("modelo")), None)
        modelo, requested, incompatibility, _needs = _checkout_model_decision(
            cfg, from_, requested, q=q, modelos=modelos
        )
        if not modelo:
            if not requested and not incompatibility:
                txt = DISCOUNT_CANONICAL + "\n\nQual modelo você está considerando?"
                _history_append(from_, "assistant", txt)
                _send_reply(cfg, from_, txt)
                return {"ok": True, "status": "btn_comprar"}
            _send_no_compatible_checkout(cfg, from_, requested, incompatibility)
            return {"ok": True, "status": "btn_sem_modelo"}
        if incompatibility:
            _q_set(from_, modelo=modelo.get("id") or "")
        link = _choose_link(modelo, q)
        if modelo and link:
            txt = _model_discount_copy(cfg, modelo, link)
            if incompatibility:
                txt = incompatibility + "\n\n" + txt
        elif modelo:
            nome = (modelo.get("nome") or "modelo").strip()
            txt = f"Vou confirmar o link e as condições atuais da {nome}. Quer que eu confira essa opção?"
            if incompatibility:
                txt = incompatibility + "\n\n" + txt
        else:
            txt = DISCOUNT_CANONICAL + "\n\nQual modelo você está considerando?"
        _history_append(from_, "assistant", txt)
        _send_reply(cfg, from_, txt)
        _log_event("link_modelo", from_, modelo.get("id", ""))
        return {"ok": True, "status": "btn_comprar"}

    if button_id in ("os_android", "os_iphone"):
        _history_append(from_, "user", f"[botão] so={button_id}")
        if q.get("step") != "app_os":
            clarification = "Pra te enviar o link do app, preciso confirmar a compra primeiro. Você já concluiu o pedido?"
            _history_append(from_, "assistant", clarification)
            _send_reply(cfg, from_, clarification)
            return {"ok": True, "status": "os_button_requires_purchase"}
        _ios = (cfg.get("link_app_ios") or "").strip()
        _and = (cfg.get("link_app_android") or "").strip()
        _uni = (cfg.get("link_app") or "").strip()
        _applink = (_ios if button_id == "os_iphone" else _and) or _uni
        _t4 = (f"Aqui está o link do app 👇\n{_applink}\n\nQualquer coisa na ativação, me chama aqui que eu ajudo. 🙂" if _applink
               else "Qualquer coisa na ativação, me chama aqui que eu ajudo. 🙂")
        _history_append(from_, "assistant", _t4)
        _send_reply(cfg, from_, _t4)
        _q_set(from_, step="posvenda")
        _log_event("app_link", from_, "")
        return {"ok": True, "status": "app_link_btn"}

    if button_id == "duvidas":
        _history_append(from_, "user", "[botão] tirar dúvidas")
        _history_append(from_, "assistant", "Pode perguntar! O que você quer saber?")
        _send_reply(cfg, from_, "Pode perguntar! O que você quer saber? 🙂")
        _q_set(from_, step="duvidas")
        return {"ok": True, "status": "btn_duvidas"}

    if button_id == "mais_sim":
        _history_append(from_, "user", "[botão] mais dúvidas=sim")
        _history_append(from_, "assistant", "Pode mandar! O que mais você quer saber?")
        _send_reply(cfg, from_, "Pode mandar! O que mais você quer saber? 🙂")
        _q_set(from_, step="duvidas")
        return {"ok": True, "status": "btn_mais_duvidas_sim"}

    if button_id == "mais_nao":
        if gate := _guard_ton_checkout(cfg, from_):
            return gate
        _history_append(from_, "user", "[botão] mais dúvidas=não")
        modelo = next((m for m in modelos if m.get("id") == q.get("modelo")), None)
        if not modelo:
            texto = DISCOUNT_CANONICAL + "\n\nQual modelo você está considerando?"
            _history_append(from_, "assistant", texto)
            _send_reply(cfg, from_, texto)
            return {"ok": True, "status": "btn_mais_duvidas_sem_modelo"}
        modelo, requested, incompatibility, _needs = _checkout_model_decision(
            cfg, from_, modelo, q=q, modelos=modelos
        )
        if not modelo:
            _send_no_compatible_checkout(cfg, from_, requested, incompatibility)
            return {"ok": True, "status": "btn_sem_modelo"}
        if incompatibility:
            _q_set(from_, modelo=modelo.get("id") or "")
        link = _choose_link(modelo, q)
        if not link:
            nome = (modelo.get("nome") or "modelo").strip()
            texto = f"Vou confirmar o link e as condições atuais da {nome}. Quer que eu confira essa opção?"
            if incompatibility:
                texto = incompatibility + "\n\n" + texto
            _history_append(from_, "assistant", texto)
            _send_reply(cfg, from_, texto)
            return {"ok": True, "status": "btn_mais_duvidas_link_indisponivel"}
        _history_append(from_, "assistant", "Fechado! Vou te mandar o link com uma condição especial. 🙂")
        _send_reply(cfg, from_, "Fechado! Vou te mandar o link com uma condição especial. 🙂")
        _q_set(from_, step="link_enviado")
        _thread_ctx(
            _pos_duvida_link_sequence,
            cfg, from_, modelo or {}, link,
        )
        return {"ok": True, "status": "btn_mais_duvidas_nao"}


def _pos_duvida_link_sequence(cfg: dict, from_: str, modelo: dict, link: str) -> None:
    """Pós-dúvida (botão Não): link -> foto da máquina -> pausa 5-10s -> condições + cupom -> à disposição.
    Cada etapa é independente: falha numa não aborta as seguintes."""
    if _guard_ton_checkout(cfg, from_):
        return
    modelo, requested, incompatibility, _needs = _checkout_model_decision(
        cfg, from_, modelo, modelos=cfg.get("modelos") or []
    )
    if not modelo:
        _send_no_compatible_checkout(cfg, from_, requested, incompatibility)
        return
    if incompatibility:
        _q_set(from_, modelo=modelo.get("id") or "")
    link = _choose_link(modelo, _q_get(from_))

    def tenta(etapa: str, fn, *args) -> None:
        try:
            fn(*args)
        except Exception as e:
            print(f"[pos-duvida] etapa {etapa} falhou para {from_}: {type(e).__name__} {str(e)[:150]}", flush=True)

    try:
        txt = ((incompatibility + "\n\n") if incompatibility else "") + (_model_discount_copy(cfg, modelo, link) if link else
               DISCOUNT_CANONICAL + "\n\nQual modelo você está considerando?")
        if _contains_checkout_link(txt, cfg) and _guard_ton_checkout(cfg, from_):
            return
        _history_append(from_, "assistant", txt)
        tenta("link", _send_reply, cfg, from_, txt)
        _log_event("link_pos_duvida", from_, modelo.get("id", ""))
        # Fotos de produto permanecem fora do fluxo de vendas.
        time.sleep(random.uniform(5.0, 10.0))
        aviso_carrinho = ("A promoção, a disponibilidade e o preço podem mudar; confirme o valor final no carrinho. "
                          "Se precisar de ajuda com o pedido, me chama aqui. 🙂")
        _history_append(from_, "assistant", aviso_carrinho)
        tenta("condicoes_carrinho", _send_reply, cfg, from_, aviso_carrinho)
        fim = "Se precisar de qualquer coisa, estou à disposição por aqui. 🙂"
        _history_append(from_, "assistant", fim)
        tenta("fecho", _send_reply, cfg, from_, fim)
    except Exception as e:
        print(f"[pos-duvida] falha na sequência para {from_}: {e}", flush=True)

    return {"ok": True, "status": "btn_desconhecido"}


_qualify_load()


def _send_media(cfg: dict, from_: str, media_url: str, caption: str = "", mediatype: str = "image") -> None:
    canal = cfg.get("canal") or "baileys"
    if canal == "meta":
        meta = cfg.get("meta") or {}
        version = meta.get("version") or "v25.0"
        phone = meta.get("phone_number_id") or ""
        token = meta.get("access_token") or ""
        _http_json(
            f"https://graph.facebook.com/{version}/{phone}/messages",
            {"Authorization": "Bearer " + token},
            {
                "messaging_product": "whatsapp",
                "to": from_,
                "type": "image",
                "image": {"link": media_url, "caption": caption or ""},
            },
        )
        return
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


def _resolve_audio(key: str) -> str | None:
    """Caminho do mp3 fixo: /app/audios no container, ../audios em teste local."""
    nome = AUDIOS_ARQUIVOS.get(key)
    if not nome:
        return None
    for pasta in (Path("/app/audios"), BASE.parent / "audios"):
        p = pasta / nome
        if p.exists():
            return str(p)
    return None


def _mp3_para_ogg(mp3_path: str) -> str | None:
    """Gravação fixa -> ogg/opus (formato de mensagem de voz do WhatsApp), com cache."""
    try:
        import subprocess
        from tts import CACHE_DIR
        os.makedirs(CACHE_DIR, exist_ok=True)
        st = os.stat(mp3_path)
        h = hashlib.sha256(f"{mp3_path}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()[:16]
        out = os.path.join(CACHE_DIR, h + ".ogg")
        if os.path.exists(out):
            return out
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", mp3_path,
             "-c:a", "libopus", "-b:a", "48k", out],
            capture_output=True, timeout=60,
        )
        if r.returncode != 0 or not os.path.exists(out):
            print(f"[audio-fix] ffmpeg falhou: {r.stderr.decode()[:200]}", flush=True)
            return None
        return out
    except Exception as e:
        print(f"[audio-fix] conversão falhou: {e}", flush=True)
        return None


def _send_voice_or_text(cfg: dict, from_: str, texto: str, mp3_key: str = "") -> None:
    """Voz DESATIVADA por enquanto (07/10/2026): sempre texto.
    Mantida a assinatura para não tocar os call sites; reativar aqui quando voltar."""
    _send_reply(cfg, from_, texto)


# Pausa humana entre mensagens sucessivas (a conversa não pode parecer disparo em rajada).
DELAY_MIN_S, DELAY_MAX_S = 1.5, 3.0


def _pausa() -> None:
    time.sleep(random.uniform(DELAY_MIN_S, DELAY_MAX_S))


def _dispatch_reply(cfg: dict, from_: str, audio_key: str, audio_fala: str, texto: str,
                    canal: str, modelo, nao_vende: bool, hist: list, pos_buttons: list | None = None,
                    queued_qualify: dict | None = None) -> None:
    """Envia a sequência da resposta (áudio -> texto -> check) em background,
    com uma pausa humana entre cada mensagem. Falha de um envio só vai pro log."""
    try:
        def checkout_decision():
            live_history = _history_get(from_)
            catalog = cfg.get("modelos") or []
            queued_user_ts = max(
                (item.get("ts", 0) for item in (hist or []) if item.get("role") == "user"),
                default=0,
            )
            latest_requested = next((found for item in reversed(live_history)
                                     if item.get("role") == "user"
                                     and item.get("ts", 0) > queued_user_ts
                                     and (found := _detect_model(
                                         item.get("content") or "", catalog
                                     ))), None)
            return _checkout_model_decision(
                cfg, from_, latest_requested or modelo, hist=live_history,
                modelos=catalog,
            )

        if modelo and _contains_checkout_link(texto, cfg):
            eligible, requested, incompatibility, _needs = checkout_decision()
            if not eligible:
                _send_no_compatible_checkout(cfg, from_, requested, incompatibility)
                return
            if eligible.get("id") != modelo.get("id"):
                modelo = eligible
                _q_set(from_, modelo=modelo.get("id") or "")
                link = _choose_link(modelo, _q_get(from_))
                texto = ((incompatibility + "\n\n") if incompatibility else "") + _model_checkout_copy(
                    cfg, modelo, link
                )
                audio_key = ""
                audio_fala = ""

        primeiro = True

        def manda(fn, *args, checkout_guard: bool = False, **kw) -> bool:
            nonlocal primeiro, modelo
            if not primeiro:
                _pausa()
            primeiro = False
            if checkout_guard and _guard_ton_checkout(cfg, from_):
                return False
            if checkout_guard and modelo:
                eligible, requested, incompatibility, _needs = checkout_decision()
                if not eligible:
                    _send_no_compatible_checkout(cfg, from_, requested, incompatibility)
                    return False
                if eligible.get("id") != modelo.get("id"):
                    modelo = eligible
                    _q_set(from_, modelo=modelo.get("id") or "")
                    link = _choose_link(modelo, _q_get(from_))
                    replacement = (
                        ((incompatibility + "\n\n") if incompatibility else "")
                        + _model_checkout_copy(cfg, modelo, link)
                    )
                    fn(args[0], args[1], replacement)
                    return False
            try:
                fn(*args, **kw)
            except Exception as e:
                print(f"[reply-bg] etapa {getattr(fn, '__name__', fn)} falhou para {from_}: {type(e).__name__} {str(e)[:200]}", flush=True)
                raise
            return True

        if audio_key or audio_fala:
            manda(_send_voice_or_text, cfg, from_,
                  audio_fala or AUDIOS_FALAS.get(audio_key, ""), audio_key)
        for bubble in _split_bubbles(texto):
            if not manda(
                _send_reply, cfg, from_, bubble,
                checkout_guard=_contains_checkout_link(bubble, cfg),
            ):
                return
        if (canal == "baileys" and modelo and not nao_vende
                and not _already_offered(hist, cfg.get("modelos") or [])):
            manda(_send_voice_or_text, cfg, from_, DOUBT_CHECK)
        if pos_buttons:
            manda(_send_buttons, cfg, from_, "Mais alguma dúvida?", pos_buttons)
            _history_append(from_, "assistant", "Mais alguma dúvida? [botões Sim/Não]")
    except Exception as e:
        print(f"[reply-bg] falha na sequência para {from_}: {e}", flush=True)


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
        if body.get("object") == "whatsapp_business_account":
            value = (((body.get("entry") or [{}])[0].get("changes") or [{}])[0]).get("value") or {}
            msgs = value.get("messages") or []
            if msgs and msgs[0].get("id"):
                return msgs[0]["id"]
    except Exception:
        pass
    return ""


SEEN_MSG_IDS: set = set()
SEEN_MSG_LOCK = threading.Lock()

# Dedupe persistente (sobrevive a restart): retries da Meta e texto
# idêntico do mesmo número em <30s. Arquivo em config/, escrita atômica.
# DEDUPE_PATH é atributo de módulo para o harness de testes isolar por caso.
DEDUPE_PATH = BASE / "config" / "seen_msgs.json"
_DEDUPE = None
_DEDUPE_FOR = None


def _dedupe() -> Dedupe:
    global _DEDUPE, _DEDUPE_FOR
    if _DEDUPE is None or _DEDUPE_FOR != str(DEDUPE_PATH):
        _DEDUPE = Dedupe(DEDUPE_PATH)
        _DEDUPE_FOR = str(DEDUPE_PATH)
    return _DEDUPE


def _already_seen(msg_id: str) -> bool:
    """Deduplica retries da Meta (mesmo id entregue 2x): True = já processado."""
    if not msg_id:
        return False
    with SEEN_MSG_LOCK:
        if msg_id in SEEN_MSG_IDS:
            return True
        SEEN_MSG_IDS.add(msg_id)
        if len(SEEN_MSG_IDS) > 2000:
            SEEN_MSG_IDS.clear()
        return False


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
    links = [
        (modelo.get(field) or "").strip()
        for modelo in (modelos or [])
        for field in ("link", "link_black")
        if (modelo.get(field) or "").strip()
    ]
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
@app.get("/api/human-support/library")
def get_human_support_library(request: Request):
    if not _current_user(request):
        return JSONResponse({"error": "não autenticado"}, status_code=401)
    try:
        with HUMAN_SUPPORT_REPLIES_LOCK:
            library = _human_support_read_library()
    except (OSError, ValueError, json.JSONDecodeError):
        return JSONResponse({"error": "não foi possível carregar a biblioteca"}, status_code=500)
    return {
        "version": library["version"],
        "replies": library["replies"],
        "assets": [dict(asset) for asset in HUMAN_SUPPORT_ASSETS],
    }


@app.put("/api/human-support/replies")
async def put_human_support_replies(request: Request):
    if not _current_user(request):
        return JSONResponse({"error": "não autenticado"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "JSON inválido"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "corpo deve ser um objeto"}, status_code=400)

    base_version = body.get("base_version")
    if not isinstance(base_version, int) or isinstance(base_version, bool) or base_version < 0:
        return JSONResponse({"error": "base_version inválida"}, status_code=400)
    replies = _human_support_validate_replies(body.get("replies"))
    if replies is None:
        return JSONResponse({"error": "respostas inválidas"}, status_code=400)

    with HUMAN_SUPPORT_REPLIES_LOCK:
        try:
            current = _human_support_read_library()
        except (OSError, ValueError, json.JSONDecodeError):
            return JSONResponse({"error": "não foi possível carregar a biblioteca"}, status_code=500)
        if base_version != current["version"]:
            return JSONResponse(
                {
                    "error": "a biblioteca foi alterada por outra pessoa",
                    "version": current["version"],
                    "replies": current["replies"],
                },
                status_code=409,
            )

        updated = {"version": current["version"] + 1, "replies": replies}
        temporary_path = HUMAN_SUPPORT_REPLIES_PATH.with_name(
            HUMAN_SUPPORT_REPLIES_PATH.name + ".tmp"
        )
        try:
            HUMAN_SUPPORT_REPLIES_PATH.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temporary_path, HUMAN_SUPPORT_REPLIES_PATH)
        except OSError:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            return JSONResponse({"error": "não foi possível salvar a biblioteca"}, status_code=500)
    return {"version": updated["version"], "replies": updated["replies"]}


@app.get("/api/config")
def get_config(request: Request):
    if not _current_user(request):
        return JSONResponse({"error": "não autenticado"}, status_code=401)
    return _load()


@app.put("/api/config")
async def put_config(request: Request):
    if not _current_user(request):
        return JSONResponse({"error": "não autenticado"}, status_code=401)
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


@app.post("/api/llm/models")
async def list_llm_models(request: Request):
    """Lista os modelos do servidor LLM (GET {base_url}/models, OpenAI-compatível)."""
    if not _current_user(request):
        return JSONResponse({"error": "não autenticado"}, status_code=401)
    cfg = _load()
    llm = cfg.get("llm") or {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    base = (body.get("base_url") or llm.get("base_url") or "").strip()
    key = (body.get("api_key") if body.get("api_key") is not None else llm.get("api_key")) or ""
    if not base:
        return JSONResponse({"ok": False, "erro": "Base URL vazia — informe a base URL do servidor LLM."},
                            status_code=400)
    url = base.rstrip("/") + "/models"
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + str(key)
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return JSONResponse({"ok": False, "erro": f"Não foi possível listar modelos de {url}: {e}"},
                            status_code=502)
    models = []
    for m in data.get("data") or []:
        mid = str(m.get("id") or "").strip()
        if not mid:
            continue
        models.append({
            "id": mid,
            "display": str(m.get("display_name") or mid),
            "quant": str(m.get("quant") or ""),
            "loaded": bool(m.get("loaded")),
        })
    return {"ok": True, "models": models, "current": llm.get("model") or ""}


# ---- QR code (busca direto da Evolution API, sempre fresco) ----
@app.get("/qr")
def qr(request: Request):
    if not _current_user(request):
        return JSONResponse({"error": "não autenticado"}, status_code=401)
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

    # Business events (whatsapp_business_manage_events): a notificação de entrega
    # (statuses) traz o conversation_id, que não vem no webhook de mensagem.
    if body.get("object") == "whatsapp_business_account":
        entry = (body.get("entry") or [{}])[0] or {}
        waba = entry.get("id") or ""
        value = ((entry.get("changes") or [{}])[0] or {}).get("value") or {}
        statuses = value.get("statuses") or []
        if statuses:
            st = statuses[0]
            conv_id = (st.get("conversation") or {}).get("id") or ""
            if st.get("status") == "sent" and conv_id:
                cfg = _load()
                result = _register_business_event(cfg, conv_id, waba_id=waba)
                print(f"[business-event] conv={conv_id} -> {result}", flush=True)
                return {"ok": True, "status": "business_event", "business_events": result}
            return {"ok": True, "status": "status_ignored"}

    from_, texto = _extract_message(body)
    button_id = _extract_button_id(body)
    _meta_msg_id = _extract_msg_id(body)
    if _already_seen(_meta_msg_id) or _dedupe().already_seen(_meta_msg_id):
        print(f"[dedupe] msg já processado: {_meta_msg_id}", flush=True)
        return {"ok": True, "status": "duplicado"}
    if from_ and texto and _dedupe().same_text_recent(from_, texto):
        print(f"[dedupe] texto repetido de {from_} em <30s", flush=True)
        return {"ok": True, "status": "duplicado_texto"}
    if from_ and _is_blocked(from_):
        print(f"[blocked] mensagem ignorada de {from_}", flush=True)
        return {"ok": True, "status": "bloqueado"}
    if from_ and texto and re.search(OPT_OUT_PAT, texto.strip(), re.IGNORECASE):
        _history_append(from_, "user", texto)
        _history_append(from_, "assistant", OPT_OUT_REPLY)
        _send_reply(_load(), from_, OPT_OUT_REPLY)
        _block(from_)
        _log_event("opt_out", from_, "")
        print(f"[blocked] opt-out registrado para {from_}", flush=True)
        return {"ok": True, "status": "opt_out"}
    if from_ and button_id:
        print(f"[btn] from={from_} botao={button_id!r}", flush=True)
        try:
            return _handle_button(_load(), from_, button_id)
        except Exception as e:
            print(f"[btn] erro: {e}", flush=True)
            return {"ok": False, "status": "btn_erro", "erro": str(e)}
    quoted_note = _extract_quoted(body)
    print(f"[webhook] from={from_} texto={texto!r}"
          + (f" quoted={quoted_note!r}" if quoted_note else ""), flush=True)
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
            _handoff_quiet = bool(from_) and _q_get(from_).get("step") in HANDOFF_SILENCE_STEPS
            if not _handoff_quiet:
                try:
                    _send_reply(_load(), from_, "Não consegui ouvir seu áudio, pode escrever pra mim? 🙂")
                except Exception as e:
                    print(f"[stt] fallback falhou: {e}", flush=True)
            else:
                print(f"[handoff-silence] audio sem resposta para {from_}", flush=True)
        return {"ok": True, "status": "no_text"}

    cfg = _load()
    llm = cfg.get("llm") or {}
    # fluxo de texto: origin=intento detectado; todos os _history_append seguintes herdam
    _ctx_set("texto", _detect_intent(texto, cfg.get("modelos") or []))

    if _is_forget(texto):
        _history_clear(from_)
        _q_clear(from_)
        confirm = "Pronto, esqueci nossa conversa anterior. Se precisar de ajuda com maquininha, é só chamar! 👋"
        print(f"[forget] histórico apagado para {from_}", flush=True)
        try:
            _send_reply(cfg, from_, confirm)
            return {"ok": True, "status": "forgot"}
        except Exception as e:
            return JSONResponse({"ok": False, "erro": "send: " + str(e)}, status_code=502)

    # Silêncio pós-handoff: conversa com humano (bot escalou ou painel assumiu).
    # Só registra; nunca responde. Recomeço ("esquecer") acima continua valendo.
    if _q_get(from_).get("step") in HANDOFF_SILENCE_STEPS:
        _history_append(from_, "user", texto)
        print(f"[handoff-silence] sem resposta para {from_}", flush=True)
        return {"ok": True, "status": "silenced_handoff"}

    if (not _history_get(from_) and (_is_greeting_like(texto) or _starts_with_greeting(texto))
            and not _detect_model(texto, cfg.get("modelos") or [])
            and not _extract_slots([texto])):
        _history_append(from_, "user", texto)
        _history_append(from_, "assistant", CANNED_GREETING)
        print(f"[greet] prospeccao direta para {from_}", flush=True)
        try:
            _send_buttons(cfg, from_, "Qual o seu tipo de negócio?", BTN_TIPO)
            _q_set(from_, step="tipo")
            return {"ok": True, "status": "greeted_tipo"}
        except Exception as e:
            return JSONResponse({"ok": False, "erro": "send: " + str(e)}, status_code=502)

    if re.search(CRIME_PAT, texto or "", re.IGNORECASE):
        _ctx_set("recusa", getattr(_CTX, "intent", None))
        _history_append(from_, "user", texto)
        _history_append(from_, "assistant", CANNED_REFUSAL)
        print(f"[crime] recusa + escalada para {from_}", flush=True)
        try:
            _send_voice_or_text(cfg, from_, CANNED_REFUSAL)
            return {"ok": True, "status": "refused_crime"}
        except Exception as e:
            return JSONResponse({"ok": False, "erro": "send: " + str(e)}, status_code=502)

    _inbound_intent = _detect_intent(texto, cfg.get("modelos") or [])
    if (re.search(HAS_MACHINE_PAT, texto or "", re.IGNORECASE)
            and not re.search(EXPLICIT_NO_INTEREST_PAT, texto or "", re.IGNORECASE)
            and _inbound_intent not in ("suporte", "posvenda", "reclamacao", "compra", "modelo",
                                        "link", "preco", "desconto", "taxa")):
        _reason_prompt = (
            "Entendi que você já tem uma maquininha. O que te faz pensar em trocar: taxa, mobilidade, "
            "comprovante ou uma segunda máquina? Assim eu sigo pelo que você precisa. 🙂"
        )
        _history_append(from_, "user", texto)
        _history_append(from_, "assistant", _reason_prompt)
        _q_set(from_, step="motivo_troca")
        _send_reply(cfg, from_, _reason_prompt)
        return {"ok": True, "status": "objecao_trocar", "resposta": _reason_prompt}

    if (re.search(NO_INTEREST_PAT, texto or "", re.IGNORECASE)
            and _inbound_intent not in ("suporte", "posvenda", "reclamacao")
            and not re.search(INTEREST_ESCAPE_PAT, texto or "", re.IGNORECASE)):
        _history_append(from_, "user", texto)
        _history_append(from_, "assistant", CANNED_EXIT)
        print(f"[no-interest] saída elegante para {from_}", flush=True)
        try:
            _send_voice_or_text(cfg, from_, CANNED_EXIT)
            return {"ok": True, "status": "no_interest"}
        except Exception as e:
            return JSONResponse({"ok": False, "erro": "send: " + str(e)}, status_code=502)

    # "não" após uma oferta = sem dúvidas (positivo): encerra sem presumir desinteresse
    if (re.search(NO_DOUBTS_PAT, (texto or "").strip(), re.IGNORECASE)
            and _already_offered(_history_get(from_), cfg.get("modelos") or [])):
        _history_append(from_, "user", texto)
        _history_append(from_, "assistant", NO_DOUBTS_REPLY)
        print(f"[no-doubts] encerramento positivo para {from_}", flush=True)
        try:
            _send_voice_or_text(cfg, from_, NO_DOUBTS_REPLY)
            return {"ok": True, "status": "no_doubts"}
        except Exception as e:
            return JSONResponse({"ok": False, "erro": "send: " + str(e)}, status_code=502)

    # Saída suave ("nada por enquanto", "só olhando"...): encerra sem link nem oferta.
    # Só vale pra mensagem curta e conclusiva — "vou pensar na T3" continua no fluxo.
    _txt = (texto or "").strip()
    if len(_txt) <= 45 and re.search(SOFT_EXIT_PAT, _txt, re.IGNORECASE):
        _history_append(from_, "user", texto)
        _history_append(from_, "assistant", SOFT_EXIT_REPLY)
        print(f"[soft-exit] encerramento suave para {from_}", flush=True)
        try:
            _send_voice_or_text(cfg, from_, SOFT_EXIT_REPLY)
            _log_event("saida_suave", from_, "")
            return {"ok": True, "status": "soft_exit"}
        except Exception as e:
            return JSONResponse({"ok": False, "erro": "send: " + str(e)}, status_code=502)

    # "interessado neste modelo" SEM card de catálogo: nunca afirmar modelo às cegas.
    # Confirma a última recomendação (ou pergunta qual das opções) — determinístico, sem LLM.
    if (not quoted_note
            and re.search(INTEREST_MODEL_PAT, texto or "", re.IGNORECASE)
            and not re.search(INTEREST_ESCAPE_PAT, texto or "", re.IGNORECASE)):
        last = None
        for m in reversed(_history_get(from_)):
            if m.get("role") == "assistant":
                last = _detect_model(m.get("content") or "", cfg.get("modelos") or [])
                if last:
                    break
        if last:
            reply = f"Perfeito! Só pra confirmar: você tá falando da {(last.get('nome') or '').strip()} que te mandei, certo?"
        else:
            nomes = [(m.get("nome") or "").strip() for m in (cfg.get("modelos") or [])]
            nomes = [n for n in nomes if n] or ["T1", "T2", "T3", "T3 Smart"]
            reply = "Claro! Qual dos modelos você quer saber: " + ", ".join(nomes[:-1]) + " ou " + nomes[-1] + "?"
        _history_append(from_, "user", texto)
        _history_append(from_, "assistant", reply)
        print(f"[model-confirm] {from_} -> {reply!r}", flush=True)
        try:
            _send_voice_or_text(cfg, from_, reply)
            return {"ok": True, "status": "model_confirm"}
        except Exception as e:
            return JSONResponse({"ok": False, "erro": "send: " + str(e)}, status_code=502)

    base = llm.get("base_url") or "http://127.0.0.1:8888/v1"
    model = llm.get("model") or "Qwen/Qwen2.5-14B-Instruct-GGUF"
    llm_key = llm.get("api_key") or ""

    _history_append(from_, "user", texto)
    hist = _history_get(from_)
    user_texts = [m["content"] for m in hist if m["role"] == "user"]
    slots = _extract_slots(user_texts)
    # Mescla o estado dos botões (QUALIFY) nos slots: sem isso o fallback
    # repete "que tipo de negócio você tem?" mesmo com tipo já informado.
    _qstate = _q_get(from_)
    _tipo_labels = {"loja_fisica": "loja física", "servicos_externos": "serviços externos", "outro": "outro negócio"}
    if "negocio" not in slots and _qstate.get("tipo"):
        slots["negocio"] = _tipo_labels.get(_qstate["tipo"], _qstate["tipo"])
    if "comprovante" not in slots and _qstate.get("comprovante"):
        slots["comprovante"] = _qstate["comprovante"]
    if "volume" not in slots and _qstate.get("volume"):
        slots["volume"] = _qstate["volume"]
    
    # Toní 3.0 - Estado comercial + intenção (antes de responder)
    intent = _detect_intent(texto, cfg.get("modelos") or [])
    motivo = _detect_motivo(texto)
    lead = _lead_update(from_, intent, motivo)
    _log_event("mensagem", from_, intent)
    # Reclamação segue o fluxo de suporte (sem vendas); handoff só acontece
    # para dúvida fora da base (_is_uncovered_handoff_reply), nunca automático.

    # Hot commercial text routes must pass the same CNPJ/Ton gate as buttons.
    if intent in ("compra", "modelo", "link"):
        if gate := _guard_ton_checkout(cfg, from_):
            return gate

    # FAST-PATH garantia pré-venda: resposta determinística (~1s), sem LLM (~25s).
    # Defeito real ("quebrou", "não liga") cai em "suporte" antes — aqui é só dúvida hipotética.
    if intent == "garantia":
        _history_append(from_, "user", texto)
        _mod = next((m for m in (cfg.get("modelos") or []) if m.get("id") == _qstate.get("modelo")), None)
        if _mod:
            _gtxt = (f"Sim! A {(_mod.get('nome') or 'maquininha').strip()} tem garantia vitalícia.\n\n"
                     "Em caso de defeito, a troca é feita pelo chat oficial da Ton, sem custo adicional.")
        else:
            _gtxt = ("Sim! Todas as maquininhas Ton têm garantia vitalícia.\n\n"
                     "Em caso de defeito, a troca é feita pelo chat oficial da Ton, sem custo adicional.")
        try:
            for bubble in _split_bubbles(_gtxt):
                _send_reply(cfg, from_, bubble)
            _history_append(from_, "assistant", _gtxt)
            _lead_event(from_, "duvida_garantia")
            _log_event("duvida_garantia", from_, "")
            if _qstate.get("step") in ("duvidas", "recomendacao", "link_enviado"):
                _send_buttons(cfg, from_, "Mais alguma dúvida?", BTN_MAIS_DUVIDAS)
                _history_append(from_, "assistant", "Mais alguma dúvida? [botões Sim/Não]")
            return {"ok": True, "status": "fast_garantia"}
        except Exception as e:
            return JSONResponse({"ok": False, "erro": "send: " + str(e)}, status_code=502)
    
    system = _build_system(cfg) + _slots_note(slots) + _lead_note(lead)
    if quoted_note:
        system += f"\n\n[contexto extra] {quoted_note}"
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

    def _discount_reply(base_text: str, selected: dict | None, status: str):
        selected, requested, incompatibility, _needs = _checkout_model_decision(
            cfg, from_, selected, hist=hist, modelos=modelos
        )
        if not selected:
            if requested and incompatibility:
                _send_no_compatible_checkout(
                    cfg, from_, requested, incompatibility, prefix=base_text
                )
                return {"ok": True, "status": status}
            return _reply_simple(base_text + "\n\nQual modelo você está considerando?", status)
        if incompatibility:
            base_text += "\n\n" + incompatibility
        q = _q_get(from_)
        link = _choose_link(selected, q)
        if link and _ton_checkout_gate_kind(q):
            result = _reply_simple(base_text, status)
            return _guard_ton_checkout(cfg, from_) or result
        if link and (gate := _guard_ton_checkout(cfg, from_)):
            return gate
        base_text += "\n\n" + _model_promo_details(cfg, selected, link)
        if link:
            nome = (selected.get("nome") or "maquininha").strip()
            base_text += f"\n\nAqui está o link da {nome}: {link}"
        _q_set(from_, modelo=selected.get("id") or "")
        return _reply_simple(base_text, status)

    # Preserve the requested model's price answer while applying the common checkout rule.
    if intent == "preco" and _needs_pix(user_texts):
        t1_requested = modelo_pre or _selected_model(modelos, _q_get(from_), hist)
        if t1_requested and t1_requested.get("id") == "t1":
            compatible, requested, incompatibility, _needs = _checkout_model_decision(
                cfg, from_, t1_requested, hist=hist, modelos=modelos
            )
            if _ton_checkout_gate_kind(_q_get(from_)):
                msg = _model_price_summary(requested or t1_requested)
                if incompatibility:
                    msg += "\n\n" + incompatibility
                result = _reply_simple(msg, "hot_preco_t1_pix_gated")
                return _guard_ton_checkout(cfg, from_) or result
            if not compatible:
                _send_no_compatible_checkout(
                    cfg, from_, requested or t1_requested, incompatibility, price_first=True
                )
                return {"ok": True, "status": "hot_t1_pix_indisponivel"}
            try:
                txt = _deliver_offer(cfg, from_, t1_requested, slots, hist)
                if txt is None:
                    return {"ok": True, "status": "checkout_gated"}
                return {"ok": True, "status": "hot_preco_modelo",
                        "modelo": _q_get(from_).get("modelo"), "resposta": txt}
            except Exception as e:
                return JSONResponse({"ok": False, "erro": "send: " + str(e)}, status_code=502)

    # A resposta ao motivo informado retoma a recomendação só com necessidades explícitas.
    if (_qstate.get("step") == "motivo_troca"
            and intent not in ("suporte", "posvenda", "reclamacao")):
        motivo_atual = _detect_motivo(texto)
        if motivo_atual:
            alvo, _requested, _incompatibility, _needs = _checkout_model_decision(
                cfg, from_, hist=hist, modelos=modelos,
                respect_history_model=False,
            )
            model_id = (alvo or {}).get("id")
            if alvo:
                _q_set(from_, step="recomendacao", modelo=model_id)
                intro = ("Como você precisa de Pix, a T2 é uma opção compatível com o que contou."
                         if model_id == "t2" and _needs_pix(user_texts) else "")
                try:
                    txt = _deliver_offer(cfg, from_, alvo, slots, hist, lead_in=intro)
                    if txt is None:
                        return {"ok": True, "status": "checkout_gated"}
                    return {"ok": True, "status": "objecao_trocar_recomendacao",
                            "modelo": _q_get(from_).get("modelo"), "resposta": txt}
                except Exception as e:
                    return JSONResponse({"ok": False, "erro": "send: " + str(e)}, status_code=502)
        return _reply_simple(
            "Entendi. Para indicar uma opção sem presumir o que você precisa, o comprovante pode ser digital ou precisa sair impresso?",
            "objecao_trocar_qualifica",
        )

    if intent == "catalogo":
        return _reply_simple(_catalog_reply(modelos, texto), "hot_catalogo_modelos")

    if intent == "taxa":
        return _reply_simple(TAXA_SAFE_REPLY, "hot_taxa")

    if intent == "desconto":
        msg = DISCOUNT_CANONICAL
        if re.search(r"cashback", texto or "", re.IGNORECASE):
            msg += " Não consigo confirmar uma condição de cashback para informar."
        selected = modelo_pre or _selected_model(modelos, _q_get(from_), hist)
        return _discount_reply(msg, selected, "hot_desconto")

    if intent == "preco" and re.search(r"car[oa]", texto or "", re.IGNORECASE):
        modelo_objeção = modelo_pre or _selected_model(modelos, _q_get(from_), hist)
        msg = _model_price_summary(modelo_objeção) if modelo_objeção else _price_overview(cfg, include_qualification=False)
        msg += "\n\n" + DISCOUNT_CANONICAL
        if re.search(r"cashback", texto or "", re.IGNORECASE):
            msg += " Não consigo confirmar uma condição de cashback para informar."
        return _discount_reply(msg, modelo_objeção, "hot_preco_objecao")

    if intent == "link":
        selected, requested, incompatibility, _needs = _checkout_model_decision(
            cfg, from_, modelo_pre or _selected_model(modelos, _q_get(from_), hist),
            hist=hist, modelos=modelos,
        )
        if not selected and requested and incompatibility:
            _send_no_compatible_checkout(cfg, from_, requested, incompatibility)
            return {"ok": True, "status": "checkout_incompatible"}
        if selected:
            if gate := _guard_ton_checkout(cfg, from_):
                return gate
            link = _choose_link(selected, _q_get(from_))
            msg = (incompatibility + "\n\n" if incompatibility else "") + (
                f"Aqui está o link da {(selected.get('nome') or 'maquininha').strip()} 👇\n\n"
                f"{_model_checkout_copy(cfg, selected, link)}"
                if link else "Vou confirmar o link correto desse modelo e já te envio."
            )
            _history_append(from_, "assistant", msg)
            _send_reply(cfg, from_, msg)
            if link:
                _q_set(from_, step="link_enviado", modelo=selected.get("id"))
            return {"ok": True, "status": "hot_link_reenvio", "modelo": selected.get("id")}

    # Cold-open (1ª mensagem): se já é uma objeção comercial, responde direto, sem auto-apresentação
    if len(user_texts) == 1 and intent == "trocar":
        return _reply_simple(
            "Entendi! Já que você tem uma maquininha, me conta: o que te faz pensar em trocar? "
            "(taxa, mobilidade, comprovante ou uma segunda máquina) Assim eu já te indico o melhor caminho. 🙂",
            "objecao_trocar",
        )

    # Confirmação após recomendação: entrega checkout do modelo selecionado sem pedir o SO.
    if (_already_offered(hist, cfg.get("modelos") or [])
            and re.match(r"^(sim|isso|vamos|bora|quero(?: comprar)?|fechado|pode mandar|manda|claro|com certeza)[.!]*$",
                         (texto or "").strip(), re.IGNORECASE)):
        if gate := _guard_ton_checkout(cfg, from_):
            return gate
        requested = _selected_model(modelos, _q_get(from_), hist) or modelo_pre
        selected, requested, incompatibility, _needs = _checkout_model_decision(
            cfg, from_, requested, hist=hist, modelos=modelos
        )
        if not selected:
            _send_no_compatible_checkout(cfg, from_, requested, incompatibility)
            return {"ok": True, "status": "checkout_incompatible"}
        link = _choose_link(selected, _q_get(from_)) if selected else ""
        if link:
            prefix = incompatibility + "\n\n" if incompatibility else ""
            msg = prefix + f"Perfeito! Aqui está o link pra fechar sua compra 👇\n\n{_model_checkout_copy(cfg, selected, link)}"
            _history_append(from_, "assistant", msg)
            _send_reply(cfg, from_, msg)
            _q_set(from_, step="link_enviado", modelo=selected.get("id"))
            _log_event("link_pos_sim", from_, selected.get("id", ""))
            return {"ok": True, "status": "link_pos_sim", "modelo": selected.get("id")}

    # Perguntou preço (com ou sem modelo): responde o valor primeiro
    if intent == "preco":
        modelo_pre = modelo_pre or _selected_model(modelos, _q_get(from_), hist)
        if _ton_checkout_gate_kind(_q_get(from_)):
            if modelo_pre:
                txt = _model_price_summary(modelo_pre)
                compatible, requested, incompatibility, _needs = _checkout_model_decision(
                    cfg, from_, modelo_pre, hist=hist, modelos=modelos
                )
                if incompatibility:
                    txt += "\n\n" + incompatibility
                if not compatible and requested and incompatibility:
                    txt += "\n\n" + (
                        "Não encontrei uma opção compatível no catálogo atual; "
                        "posso encaminhar sua dúvida para atendimento humano."
                    )
                status = "hot_preco_modelo_gated"
            else:
                txt = _price_overview(cfg, include_qualification=False)
                status = "hot_preco_faixa_gated"
            _history_append(from_, "assistant", txt)
            _send_reply(cfg, from_, txt)
            gate = _guard_ton_checkout(cfg, from_)
            return {"ok": True, "status": status, "modelo": (modelo_pre or {}).get("id"), "resposta": txt}
        if modelo_pre:
            try:
                txt = _deliver_offer(cfg, from_, modelo_pre, slots, hist)
                if txt is None:
                    return {"ok": True, "status": "checkout_gated"}
                return {"ok": True, "status": "hot_preco_modelo",
                        "modelo": _q_get(from_).get("modelo"), "resposta": txt}
            except Exception as e:
                return JSONResponse({"ok": False, "erro": "send: " + str(e)}, status_code=502)
        return _reply_simple(_price_overview(cfg), "hot_preco_faixa", "oferta_faixa")

    # Quer comprar e já deu o essencial (modelo citado ou comprovante): entrega já
    if intent in ("compra", "modelo"):
        lead_in = ""
        if modelo_pre and modelo_pre.get("id") == "t1" and _needs_pix(user_texts):
            compatible, requested, incompatibility, _needs = _checkout_model_decision(
                cfg, from_, modelo_pre, hist=hist, modelos=modelos
            )
            if not compatible:
                _send_no_compatible_checkout(
                    cfg, from_, requested or modelo_pre, incompatibility
                )
                return {"ok": True, "status": "hot_t1_pix_indisponivel"}
            alvo = modelo_pre
        else:
            alvo = (modelo_pre or _selected_model(modelos, _q_get(from_), hist)
                    or (_choose_model(slots, user_texts, modelos)
                        if (slots.get("comprovante") or "") else None))
        if alvo:
            try:
                txt = _deliver_offer(cfg, from_, alvo, slots, hist, with_audio=False, lead_in=lead_in)
                if txt is None:
                    return {"ok": True, "status": "checkout_gated"}
                return {"ok": True, "status": "hot_compra_modelo",
                        "modelo": _q_get(from_).get("modelo"), "resposta": txt}
            except Exception as e:
                return JSONResponse({"ok": False, "erro": "send: " + str(e)}, status_code=502)


    # Pediu link sem modelo conhecido: nunca link às cegas.
    # Oferta já feita ("cadê o link?") → reenvia; senão, qualifica primeiro.
    if intent == "link" and not modelo_pre and not _q_get(from_).get("modelo"):
        if _already_offered(hist, cfg.get("modelos") or []):
            _history_append(from_, "user", texto)
            _ultimo = ""
            modelo_anterior = None
            links_configurados = [
                (url, model)
                for model in (cfg.get("modelos") or [])
                for url in (model.get("link"), model.get("link_black"))
                if url
            ]
            for m in reversed(hist):
                if m.get("role") == "assistant":
                    for _u in re.findall(r"https?://\S+", m.get("content") or ""):
                        _candidate = _u.rstrip(".,!?)]}")
                        _matched = next(((url, model) for url, model in links_configurados
                                         if _candidate == url), None)
                        if _matched:
                            _ultimo, modelo_anterior = _matched
                            break
                if _ultimo:
                    break
            if modelo_anterior and _ultimo:
                eligible, requested, incompatibility, _needs = _checkout_model_decision(
                    cfg, from_, modelo_anterior, hist=hist, modelos=cfg.get("modelos") or []
                )
                if not eligible:
                    _send_no_compatible_checkout(cfg, from_, requested, incompatibility)
                    return {"ok": True, "status": "checkout_incompatible"}
                current_link = _choose_link(eligible, _q_get(from_))
                msg = ((incompatibility + "\n\n") if incompatibility else "") + _model_discount_copy(
                    cfg, eligible, current_link
                )
                _q_set(from_, step="link_enviado", modelo=eligible.get("id") or "")
            else:
                msg = DISCOUNT_CANONICAL + "\n\nQual modelo você está considerando?"
            _history_append(from_, "assistant", msg)
            _send_reply(cfg, from_, msg)
            return {"ok": True, "status": "link_reenvio"}
        _history_append(from_, "user", texto)
        _history_append(from_, "assistant", "Claro! Só pra te mandar o link certo: qual o seu tipo de negócio?")
        _send_buttons(cfg, from_, "Qual o seu tipo de negócio?", BTN_TIPO)
        _q_set(from_, step="tipo")
        _log_event("link_sem_modelo", from_, "")
        return {"ok": True, "status": "link_sem_modelo"}

    # Trocar sem nenhum dado: 1 pergunta antes de ofertar (não pitch às cegas).
    if intent == "trocar" and not modelo_pre and not slots.get("comprovante") and not _q_get(from_).get("modelo"):
        return _reply_simple(
            "Entendi! Já que você tem uma maquininha, me conta: o que te faz pensar em trocar? "
            "(taxa, mobilidade, comprovante ou uma segunda máquina) Assim eu já te indico o melhor caminho. 🙂",
            "objecao_trocar_qualifica",
        )


    # "Sim" após oferta/link: MANDA O LINK DE COMPRA na hora (nunca pergunta de OS aqui).
    if (re.match(r"^(sim|isso|vamos|bora|quero|fechado|pode mandar|manda|claro|com certeza)[.!]*$", (texto or "").strip(), re.IGNORECASE)
            and _already_offered(hist, cfg.get("modelos") or [])):
        if gate := _guard_ton_checkout(cfg, from_):
            return gate
        _history_append(from_, "user", texto)
        _requested2 = _selected_model(modelos, _q_get(from_), hist) or modelo_pre
        _mod2, _requested2, _incompatibility2, _needs2 = _checkout_model_decision(
            cfg, from_, _requested2, hist=_history_get(from_), modelos=modelos
        )
        if not _mod2:
            _send_no_compatible_checkout(cfg, from_, _requested2, _incompatibility2)
            return {"ok": True, "status": "checkout_incompatible"}
        _link2 = _choose_link(_mod2, _q_get(from_))
        _t2 = (
            ((_incompatibility2 + "\n\n") if _incompatibility2 else "")
            + "Perfeito! Aqui está o link pra fechar sua compra 👇\n\n"
            + _model_checkout_copy(cfg, _mod2, _link2)
        ) if _link2 else "Perfeito! Já te mando o link pra fechar sua compra. 🙂"
        _history_append(from_, "assistant", _t2)
        _send_reply(cfg, from_, _t2)
        _q_set(from_, step="link_enviado", modelo=_mod2.get("id") or "")
        _log_event("link_pos_sim", from_, (_mod2 or {}).get("id", ""))
        return {"ok": True, "status": "link_pos_sim", "modelo": _mod2.get("id")}

    # Compra confirmada ("comprei", "finalizei"...): pergunta o SO explicando o motivo.
    if re.search(r"comprei|comprado|finalizei|finalizado|j[áa] pedi|pedido feito|acabei de (comprar|pedir)|pagamento feito|j[áa] comprei", (texto or ""), re.IGNORECASE):
        _history_append(from_, "user", texto)
        _t3 = ("Boa, parabéns pela compra! 🎉\n\nSeu celular é Android ou iPhone? É pra eu te mandar o link certo do app da Ton. 🙂")
        _history_append(from_, "assistant", _t3)
        _send_buttons(cfg, from_, "Seu celular é Android ou iPhone?", [("os_android", "Android"), ("os_iphone", "iPhone")])
        _q_set(from_, step="app_os")
        _log_event("compra_confirmada", from_, "")
        return {"ok": True, "status": "compra_confirmada"}

    # Resposta do SO: manda o link do app + disponibilidade.
    if _q_get(from_).get("step") == "app_os" and re.search(r"android|iphone|ios|apple", (texto or ""), re.IGNORECASE):
        _history_append(from_, "user", texto)
        _ios = (cfg.get("link_app_ios") or "").strip()
        _and = (cfg.get("link_app_android") or "").strip()
        _uni = (cfg.get("link_app") or "").strip()
        _applink = (_ios if re.search(r"iphone|ios|apple", texto, re.IGNORECASE) else _and) or _uni
        _t4 = (f"Aqui está o link do app 👇\n{_applink}\n\nQualquer coisa na ativação, me chama aqui que eu ajudo. 🙂" if _applink
               else "Qualquer coisa na ativação, me chama aqui que eu ajudo. 🙂")
        _history_append(from_, "assistant", _t4)
        _send_reply(cfg, from_, _t4)
        _q_set(from_, step="posvenda")
        _log_event("app_link", from_, "")
        return {"ok": True, "status": "app_link"}


    # Raciocínio só nos casos complexos: simples responde rápido, difícil responde certo
    complex_case = len(slots) >= 3 or len(texto) > 150 or len(hist) > 10
    print(f"[llm] thinking={complex_case} slots={list(slots)} intent={intent}", flush=True)
    try:
        llm_resp = _http_json(
            base + "/chat/completions",
            {"Authorization": "Bearer " + llm_key},
            {"model": model, "messages": messages, "max_tokens": 1200},
        )
    except Exception as e:
        print(f"[llm] falhou: {str(e)[:300]}", flush=True)
        return JSONResponse({"ok": False, "erro": "llm: " + str(e)}, status_code=502)



    choice = (llm_resp.get("choices") or [{}])[0] or {}
    resposta = ((choice.get("message") or {}).get("content") or "").strip()
    # NUNCA enviar reasoning interno ao cliente: se o conteúdo veio vazio, sem resposta
    if not resposta:
        return {"ok": True, "status": "llm_empty"}

    # Marcador de áudio sai do texto ANTES dos transforms: chave = gravação fixa do momento,
    # fala livre = TTS; o resto segue como texto.
    audio_key, audio_fala, resposta = _split_audio_marker(resposta)
    closing_audio_suppressed = False
    if audio_key == "fechamento":
        selected_state = _q_get(from_)
        if selected_state.get("step") == "duvidas" or not selected_state.get("modelo"):
            audio_key = ""
            closing_audio_suppressed = True
            if not resposta and not audio_fala:
                resposta = "Pode mandar sua dúvida por aqui."
    if audio_fala:
        audio_fala = _strip_unverified_commercial_claims(audio_fala)
        audio_fala = _sanitize_llm_urls(audio_fala, cfg)
    if audio_key or audio_fala:
        print(f"[audio] {from_} key={audio_key or '-'} fala={audio_fala[:80]!r}", flush=True)
    if not resposta and not audio_fala and not audio_key:
        return {"ok": True, "status": "llm_empty"}

    resposta = _strip_markdown(resposta)
    resposta = _soften_superlative(resposta)
    is_fresh = len([m for m in hist if m["role"] == "user"]) <= 1
    cold_commercial = is_fresh and intent not in ("info", "")
    resposta = _strip_reintro(resposta, not cold_commercial,
                              cliente_cumprimentou=_starts_with_greeting(texto))
    resposta = _strip_repeated_questions(resposta, slots)
    resposta = _strip_cadastro_question(resposta, slots)
    resposta = _keep_first_question(resposta)
    modelo_pre = _detect_model(resposta, cfg.get("modelos") or [])
    resposta = _strip_unverified_commercial_claims(resposta)
    modelo_pre = _detect_model(resposta, cfg.get("modelos") or []) or modelo_pre
    # só a PRIMEIRA recomendação encerra sem pergunta; em follow-up (oferta já feita),
    # a pergunta de avanço do LLM deve sobreviver (ex.: "estou comparando").
    if modelo_pre and not _already_offered(hist, cfg.get("modelos") or []):
        resposta = _strip_all_questions(resposta)
        if not resposta:
            resposta = _recommendation_fallback(modelo_pre, slots)
    if not resposta:
        # com áudio na mensagem, a pergunta é do áudio: não injeta pergunta de texto em cima
        resposta = "" if (audio_key or audio_fala) else _fallback_question(slots)

    try:
        canal = cfg.get("canal") or "baileys"
        modelo = _detect_model(resposta, cfg.get("modelos") or []) or modelo_pre
        checkout_response = False
        checkout_response_link = ""

        _q_out = _q_get(from_)
        _would_attach_checkout = bool(
            modelo
            and intent not in ("posvenda", "suporte", "reclamacao")
            and _q_out.get("step") != "duvidas"
            and (not _already_offered(hist, cfg.get("modelos") or []) or intent == "link")
        )
        if (_would_attach_checkout or re.search(
                r"https?://(?:www\.)?ton\.com\.br/checkout\b", resposta, re.IGNORECASE)):
            if gate := _guard_ton_checkout(cfg, from_):
                return gate

        texto = resposta
        for m in cfg.get("modelos") or []:
            url = (m.get("foto") or "").strip()
            if url and url in texto:
                texto = texto.replace(url, "").strip()

        # Toní 3.0 (regra 8): pós-venda/suporte/reclamação NÃO executa fluxo de vendas
        # (sem link de compra, sem Pix, sem fechamento de recomendação).
        support_path = intent in ("posvenda", "suporte", "reclamacao") or _q_out.get("step") in {
            "handoff", "atendimento", "atendimento_humano", "escalado", "posvenda",
        }
        nao_vende = support_path
        # MODO DÚVIDA (pós-oferta): resposta pura, sem link/foto/Pix/preço.
        # O link só sai no botão Não ("vou te mandar o link"). Os botões
        # Sim/Não dão continuidade — nada de oferta embutida aqui.
        _modo_duvida = (_q_get(from_).get("step") == "duvidas")
        if modelo and not nao_vende and not _modo_duvida:
            eligible_model, requested_model, incompatibility, _needs = _checkout_model_decision(
                cfg, from_, modelo, q=_q_get(from_), hist=hist,
                modelos=cfg.get("modelos") or [],
            )
            if not eligible_model:
                _send_no_compatible_checkout(cfg, from_, requested_model, incompatibility)
                return {"ok": True, "status": "checkout_incompatible"}
            if eligible_model.get("id") != modelo.get("id"):
                requested_name = (requested_model or {}).get("nome") or ""
                if requested_name:
                    kept = [sentence for sentence in _split_sentences(texto)
                            if not re.search(re.escape(requested_name), sentence, re.IGNORECASE)]
                    texto = " ".join(kept).strip()
                texto = "\n\n".join(part for part in (incompatibility, texto) if part)
            modelo = eligible_model
            _q_set(from_, modelo=modelo.get("id") or "")
            # link/fecho só na primeira oferta ou quando o cliente pede o link de novo;
            # em follow-up (oferta já feita) não re-envia link nem Pix nem fecho.
            ja_oferta = _already_offered(hist, cfg.get("modelos") or [])
            if not ja_oferta or intent == "link":
                selected_link = _choose_link(modelo, _q_get(from_))
                checkout_response = True
                checkout_response_link = selected_link
                texto = _strip_urls(texto)
                texto = _ensure_pix_note(texto, modelo)
                checkout_copy = _model_checkout_copy(cfg, modelo, selected_link)
                if checkout_copy:
                    texto = (texto.rstrip() + "\n\n" + checkout_copy).strip()
                low = texto.lower()
                sem_despedida = not any(w in low for w in ("obrigado", "valeu", "por aqui",
                                                            "disposi", "precisando"))
                # link já no texto ou áudio fazendo a pergunta da mensagem: nada a acrescentar
                if sem_despedida and "http" not in low and not (audio_key or audio_fala):
                    texto = texto.rstrip() + "\n\n" + _recommendation_fallback(modelo, slots)
        user_last = next((m["content"] for m in reversed(hist) if m["role"] == "user"), "")
        last_modelo = modelo
        if not last_modelo:
            for m in reversed(hist):
                if m["role"] == "assistant":
                    last_modelo = _detect_model(m["content"], cfg.get("modelos") or [])
                    if last_modelo:
                        break
        if last_modelo and "R$" not in texto and not _modo_duvida and not support_path:
            anchor = _price_anchor(user_last, last_modelo)
            if anchor:
                texto = anchor + "\n\n" + texto
        texto = _apply_app_link(texto, cfg, slots, hist)
        if checkout_response:
            audio_fala = _strip_urls(audio_fala)
            texto = _sanitize_llm_urls(texto, cfg)
            texto = _strip_urls(texto)
            if checkout_response_link:
                texto = (texto.rstrip() + "\n\n" + checkout_response_link).strip()
        else:
            texto = _sanitize_llm_urls(texto, cfg)
        if support_path:
            audio_fala = _strip_support_sales_pitch(audio_fala)
            audio_key = ""
            texto = _strip_support_sales_pitch(texto)
            if texto.strip() == _fallback_question(slots):
                texto = ""
            if not texto and not audio_fala:
                texto = "Entendi. Vou encaminhar seu caso para atendimento humano."

        # Guarda anti-beco: mensagem final sem pergunta e sem link = beco sem saída.
        # (Mensagens de pós-venda/suporte/reclamação são terminal legítimo — não entra aqui.)
        # Oferta já feita: NUNCA re-pergunta "quer o link?" — os botões Sim/Não dão continuidade.
        _ja_ofertado = _already_offered(hist, cfg.get("modelos") or [])
        if ("?" not in texto and "http" not in texto.lower()
                and not nao_vende and not audio_fala and not audio_key
                and not _ja_ofertado and not _modo_duvida
                and not closing_audio_suppressed):
            texto = texto.rstrip() + "\n\n" + _fallback_question(slots)

        falas_hist = AUDIOS_FALAS.get(audio_key) or audio_fala
        # Dúvida fora da base: a resposta declara o encaminhamento — marca handoff
        # para o bot ficar em silêncio nas próximas mensagens (humano assume).
        if _is_uncovered_handoff_reply(texto):
            _q_set(from_, step="handoff")
            _log_event("escalonamento", from_, "")
            print(f"[handoff] duvida fora da base para {from_}", flush=True)
        print(f"[reply] to={from_} modelo={(modelo or {}).get('id')} audio={audio_key or bool(audio_fala)} texto={texto!r}", flush=True)
        _history_append(from_, "assistant", ((falas_hist + " ") if falas_hist else "") + texto)

        # Sequência em background com pausa humana entre mensagens: o webhook responde já
        # e nada sai disparado em rajada (áudio -> texto -> card -> check).
        # Pós-dúvida: botões Sim/Não ("mais alguma dúvida?") em vez de texto solto.
        # Continuidade pós-oferta: botões Sim/Não sempre que a resposta não
        # termina com pergunta — vale pra dúvidas, recomendação e pós-link.
        # (Antes era só step==duvidas: resposta sem pergunta em outro step morria sem saída.)
        _pos_buttons = (BTN_MAIS_DUVIDAS if (_q_get(from_).get("step") in ("duvidas", "recomendacao", "link_enviado")
                                                   and not nao_vende
                                                   and "?" not in texto) else None)
        _thread_ctx(
            _dispatch_reply,
            cfg, from_, audio_key, audio_fala, texto, canal, modelo, nao_vende, hist,
            _pos_buttons, _q_get(from_),
        )
        return {"ok": True, "status": "sent", "modelo": (modelo or {}).get("id"), "resposta": texto}
    except Exception as e:
        import traceback as _tb
        print(f"[reply-erro] {type(e).__name__}: {e}\n{_tb.format_exc()}", flush=True)
        _history_append(from_, "assistant", resposta)
        return JSONResponse({"ok": False, "erro": "send: " + str(e)}, status_code=502)


# ---- Autenticação (rotas) ----
@app.get("/login")
def login_page():
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/cadastro")
def register_page():
    return FileResponse(STATIC_DIR / "register.html")


@app.get("/privacidade")
def privacy_page():
    return FileResponse(STATIC_DIR / "privacy.html")


@app.get("/cookies")
def cookies_page():
    return FileResponse(STATIC_DIR / "cookies.html")


@app.get("/termos")
def terms_page():
    return FileResponse(STATIC_DIR / "terms.html")


@app.get("/quem-somos")
def about_page():
    return FileResponse(STATIC_DIR / "about.html")


@app.get("/exclusao-de-dados")
def delete_data_page():
    return FileResponse(STATIC_DIR / "delete-data.html")


@app.get("/profile")
def profile_page(request: Request):
    if not _current_user(request):
        return RedirectResponse("/login", status_code=302)
    return FileResponse(STATIC_DIR / "profile.html")


@app.get("/api/auth/me")
def auth_me(request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse({"authenticated": False}, status_code=401)
    return {
        "authenticated": True,
        "user": {
            "email": user.get("email"),
            "name": user.get("name"),
            "provider": user.get("provider"),
            "picture_url": user.get("picture_url"),
        },
    }


@app.post("/api/auth/login")
async def auth_login(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "JSON inválido"}, status_code=400)
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    conn = _db()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    if not row or not _verify_password(password, row["password_hash"] or ""):
        return JSONResponse({"error": "E-mail ou senha inválidos"}, status_code=401)
    token = _create_session(row["id"])
    resp = JSONResponse({"ok": True, "redirect": "/"})
    _set_session_cookie(resp, token)
    return resp


@app.post("/api/auth/register")
async def auth_register(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "JSON inválido"}, status_code=400)
    name = (body.get("name") or "").strip()
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    if not name:
        return JSONResponse({"error": "Informe seu nome"}, status_code=400)
    if not re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return JSONResponse({"error": "E-mail inválido"}, status_code=400)
    if len(password) < 6:
        return JSONResponse({"error": "A senha deve ter ao menos 6 caracteres"}, status_code=400)
    conn = _db()
    existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        conn.close()
        return JSONResponse({"error": "Este e-mail já está cadastrado"}, status_code=409)
    cur = conn.execute(
        "INSERT INTO users (email, password_hash, name, provider, created_at) VALUES (?,?,?,?,?)",
        (email, _hash_password(password), name, "email", int(time.time())),
    )
    conn.commit()
    conn.close()
    token = _create_session(cur.lastrowid)
    resp = JSONResponse({"ok": True, "redirect": "/"})
    _set_session_cookie(resp, token)
    return resp


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    _delete_session(request)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session_token")
    return resp


def _fb_redirect_uri(request: Request) -> str:
    """Redirect URI derivado do host atual (funciona em toni. e wa.)."""
    host = (request.headers.get("host") or "").strip()
    if not host:
        return ""
    return f"https://{host}/api/auth/facebook/callback"


@app.get("/api/auth/facebook")
def auth_facebook(request: Request):
    cfg = (_load().get("auth") or {})
    app_id = cfg.get("facebook_app_id") or ""
    redirect_uri = _fb_redirect_uri(request) or cfg.get("redirect_uri") or ""
    if not app_id:
        return RedirectResponse("/login?erro=facebook-nao-configurado", status_code=302)
    state = secrets.token_urlsafe(16)
    scope = "public_profile"
    url = (
        f"https://www.facebook.com/v25.0/dialog/oauth"
        f"?client_id={urllib.parse.quote(app_id, safe='')}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
        f"&scope={urllib.parse.quote(scope, safe='')}"
        f"&state={state}"
    )
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie("oauth_state", state, httponly=True, samesite="lax", max_age=600)
    return resp


@app.get("/api/auth/facebook/callback")
def auth_facebook_callback(request: Request):
    fb_error = request.query_params.get("error") or ""
    fb_desc = request.query_params.get("error_description") or ""
    if fb_error:
        msg = fb_desc or fb_error
        return RedirectResponse(f"/login?erro={urllib.parse.quote(msg, safe='')}", status_code=302)
    code = request.query_params.get("code") or ""
    if not code:
        return RedirectResponse("/login?erro=oauth", status_code=302)
    cfg = (_load().get("auth") or {})
    app_id = cfg.get("facebook_app_id") or ""
    app_secret = cfg.get("facebook_app_secret") or ""
    redirect_uri = _fb_redirect_uri(request) or cfg.get("redirect_uri") or ""
    token_url = (
        f"https://graph.facebook.com/v25.0/oauth/access_token"
        f"?client_id={urllib.parse.quote(app_id, safe='')}"
        f"&client_secret={urllib.parse.quote(app_secret, safe='')}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
        f"&code={urllib.parse.quote(code, safe='')}"
    )
    try:
        with urllib.request.urlopen(token_url, timeout=30) as resp:
            token_data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return RedirectResponse(f"/login?erro={urllib.parse.quote(str(e), safe='')}",
                                status_code=302)
    access_token = token_data.get("access_token") or ""
    if not access_token:
        return RedirectResponse("/login?erro=token", status_code=302)
    me_url = (
        "https://graph.facebook.com/me?fields=id,name,email,picture&access_token="
        + urllib.parse.quote(access_token, safe="")
    )
    try:
        with urllib.request.urlopen(me_url, timeout=30) as resp:
            me = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return RedirectResponse(f"/login?erro={urllib.parse.quote(str(e), safe='')}",
                                status_code=302)
    fb_id = me.get("id") or ""
    name = me.get("name") or ""
    email = (me.get("email") or "").strip().lower()
    picture = (((me.get("picture") or {}).get("data") or {}).get("url") or "")
    now = int(time.time())
    conn = _db()
    row = None
    if fb_id:
        row = conn.execute("SELECT * FROM users WHERE facebook_id = ?", (fb_id,)).fetchone()
    if not row and email:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if row:
        uid = row["id"]
        conn.execute(
            "UPDATE users SET name=?, picture_url=?, facebook_id=?, email=? WHERE id=?",
            (name or row["name"], picture or row["picture_url"],
             fb_id or row["facebook_id"], email or row["email"], uid),
        )
    else:
        cur = conn.execute(
            "INSERT INTO users (email, name, provider, facebook_id, picture_url, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (email, name, "facebook", fb_id, picture, now),
        )
        uid = cur.lastrowid
    conn.commit()
    conn.close()
    token = _create_session(uid)
    resp = RedirectResponse("/", status_code=302)
    _set_session_cookie(resp, token)
    resp.delete_cookie("oauth_state")
    return resp


@app.post("/api/auth/facebook/deauthorize")
async def facebook_deauthorize(request: Request):
    """Facebook envia um POST com signed_request quando o usuário desautoriza o app."""
    try:
        body = (await request.body()).decode("utf-8")
        params = urllib.parse.parse_qs(body)
        signed = (params.get("signed_request") or [""])[0]
        if not signed:
            return Response(content="ok", status_code=200)
        payload_b64, signature_b64 = signed.split(".", 1)
        app_secret = ((_load().get("auth") or {}).get("facebook_app_secret") or "").encode("utf-8")
        expected = base64.urlsafe_b64encode(
            hmac.new(app_secret, payload_b64.encode("utf-8"), hashlib.sha256).digest()
        ).decode("ascii").rstrip("=")
        if not hmac.compare_digest(expected, signature_b64):
            print("[auth] deauthorize: assinatura inválida", flush=True)
            return Response(content="ok", status_code=200)
        pad = "=" * (-len(payload_b64) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload_b64 + pad))
        user_id = str(data.get("user_id") or "")
        if user_id:
            conn = _db()
            conn.execute("DELETE FROM users WHERE facebook_id = ?", (user_id,))
            conn.commit()
            conn.close()
            print(f"[auth] desautorização: usuário Facebook {user_id} removido", flush=True)
    except Exception as e:
        print(f"[auth] deauthorize erro: {e}", flush=True)
    return Response(content="ok", status_code=200)


@app.get("/mensagens")
def messages_page(request: Request):
    if not _current_user(request):
        return RedirectResponse("/login", status_code=302)
    return FileResponse(STATIC_DIR / "chat.html")


@app.get("/api/conversations")
def list_conversations(request: Request):
    if not _current_user(request):
        return JSONResponse({"error": "não autenticado"}, status_code=401)
    convs = []
    with HISTORY_LOCK:
        for phone, msgs in HISTORY.items():
            if not msgs:
                continue
            last = msgs[-1]
            convs.append({
                "phone": phone,
                "count": len(msgs),
                "last_ts": last.get("ts", 0),
                "last": (last.get("content") or "")[:90],
                "last_role": last.get("role") or "",
            })
    convs.sort(key=lambda c: c.get("last_ts") or 0, reverse=True)
    return {"conversations": convs}


@app.get("/api/conversations/{phone}")
def get_conversation(phone: str, request: Request):
    if not _current_user(request):
        return JSONResponse({"error": "não autenticado"}, status_code=401)
    return {"phone": phone, "messages": _history_get(phone)}


@app.post("/api/conversations/{phone}/send")
async def send_conversation_message(phone: str, request: Request):
    if not _current_user(request):
        return JSONResponse({"error": "não autenticado"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "JSON inválido"}, status_code=400)
    texto = (body.get("text") or "").strip()
    if not texto:
        return JSONResponse({"error": "mensagem vazia"}, status_code=400)
    if _is_blocked(phone):
        return JSONResponse({"error": "contato bloqueado"}, status_code=403)
    try:
        _send_reply(_load(), phone, texto)
    except Exception as e:
        print(f"[admin-text] envio falhou: {type(e).__name__}", flush=True)
        return JSONResponse({"error": "falha ao enviar mensagem"}, status_code=502)
    _ctx_set("admin")
    _history_append(phone, "assistant", texto)
    # Humano assumiu pelo painel: bot fica em silêncio nas próximas mensagens.
    _q_set(phone, step="handoff")
    return {"ok": True}


def _wa_api(cfg: dict, method: str, path: str, body=None) -> dict:
    """Chamada genérica à Graph API do WhatsApp (GET/POST/PUT) com o token do config."""
    meta = cfg.get("meta") or {}
    version = meta.get("version") or "v25.0"
    token = meta.get("access_token") or ""
    url = f"https://graph.facebook.com/{version}/{path}"
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


@app.get("/api/whatsapp/templates")
def wa_templates(request: Request):
    if not _current_user(request):
        return JSONResponse({"error": "não autenticado"}, status_code=401)
    cfg = _load()
    waba = (cfg.get("meta") or {}).get("waba_id") or ""
    try:
        data = _wa_api(cfg, "GET", f"{waba}/message_templates?fields=name,status,category,language")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return {"templates": data.get("data") or []}


@app.get("/api/whatsapp/business-profile")
def wa_business_profile(request: Request):
    if not _current_user(request):
        return JSONResponse({"error": "não autenticado"}, status_code=401)
    cfg = _load()
    pnid = (cfg.get("meta") or {}).get("phone_number_id") or ""
    try:
        data = _wa_api(cfg, "GET", f"{pnid}/whatsapp_business_profile?fields=about,address,description,email,websites,vertical")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return {"profile": (data.get("data") or [{}])[0]}


@app.put("/api/whatsapp/business-profile")
async def wa_business_profile_update(request: Request):
    if not _current_user(request):
        return JSONResponse({"error": "não autenticado"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "JSON inválido"}, status_code=400)
    cfg = _load()
    pnid = (cfg.get("meta") or {}).get("phone_number_id") or ""
    payload = {"messaging_product": "whatsapp"}
    for k in ("about", "address", "description", "email", "vertical"):
        if body.get(k) is not None and str(body.get(k)).strip() != "":
            payload[k] = str(body.get(k)).strip()
    if body.get("websites"):
        payload["websites"] = [str(w).strip() for w in body.get("websites") if str(w).strip()]
    try:
        data = _wa_api(cfg, "POST", f"{pnid}/whatsapp_business_profile", payload)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return {"ok": True, "result": data}


@app.get("/api/whatsapp/phone-numbers")
def wa_phone_numbers(request: Request):
    if not _current_user(request):
        return JSONResponse({"error": "não autenticado"}, status_code=401)
    cfg = _load()
    waba = (cfg.get("meta") or {}).get("waba_id") or ""
    try:
        data = _wa_api(cfg, "GET", f"{waba}/phone_numbers?fields=id,display_phone_number,verified_name,status,quality_rating,messaging_limit_tier")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return {"numbers": data.get("data") or []}


@app.post("/api/whatsapp/send-media")
async def wa_send_media(request: Request):
    if not _current_user(request):
        return JSONResponse({"error": "não autenticado"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "JSON inválido"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "corpo deve ser um objeto"}, status_code=400)
    to = body.get("to")
    asset_id = body.get("asset_id")
    caption = body.get("caption", "")
    if not isinstance(to, str) or not to.strip():
        return JSONResponse({"error": "destinatário inválido"}, status_code=400)
    to = to.strip()
    if "url" in body or not isinstance(asset_id, str) or asset_id not in HUMAN_SUPPORT_ASSET_IDS:
        return JSONResponse({"error": "imagem inválida"}, status_code=400)
    if caption is None:
        caption = ""
    if not isinstance(caption, str) or len(caption) > 1024:
        return JSONResponse({"error": "legenda inválida"}, status_code=400)
    caption = caption.strip()
    if _is_blocked(to):
        return JSONResponse({"error": "contato bloqueado"}, status_code=403)
    asset = next(item for item in HUMAN_SUPPORT_ASSETS if item["id"] == asset_id)
    cfg = _load()
    try:
        _send_media(cfg, to, asset["preview_url"], caption, mediatype="image")
    except Exception as e:
        print(f"[admin-media] envio falhou: {type(e).__name__}", flush=True)
        return JSONResponse({"error": "falha ao enviar imagem"}, status_code=502)
    _ctx_set("admin")
    content = caption or f"Imagem enviada: {asset['name']}"
    _history_append(
        to,
        "assistant",
        content,
        metadata={"kind": "image", "asset_id": asset_id},
    )
    # Humano assumiu pelo painel: bot fica em silêncio nas próximas mensagens.
    _q_set(to, step="handoff")
    return {"ok": True}


@app.post("/api/whatsapp/send-template")
async def wa_send_template(request: Request):
    if not _current_user(request):
        return JSONResponse({"error": "não autenticado"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "JSON inválido"}, status_code=400)
    to = (body.get("to") or "").strip()
    name = (body.get("name") or "").strip()
    lang = (body.get("language") or "pt_BR").strip()
    if not to or not name:
        return JSONResponse({"error": "to e name são obrigatórios"}, status_code=400)
    cfg = _load()
    pnid = (cfg.get("meta") or {}).get("phone_number_id") or ""
    try:
        data = _wa_api(cfg, "POST", f"{pnid}/messages", {
            "messaging_product": "whatsapp", "to": to, "type": "template",
            "template": {"name": name, "language": {"code": lang}},
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return {"ok": True, "result": data}


@app.get("/")
def index(request: Request):
    if not _current_user(request):
        return RedirectResponse("/login", status_code=302)
    host = (request.headers.get("host") or "").lower()
    if host.startswith("toni."):
        return FileResponse(STATIC_DIR / "app.html")
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


_init_db()
