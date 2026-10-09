"""Deduplicação de inbound do WhatsApp (retries da Meta / duplo disparo).

Duas barreiras, ambas persistidas em disco para sobreviver a restart:
1. ``already_seen(msg_id)`` — mesmo id Meta entregue 2x (retry oficial).
2. ``same_text_recent(phone, texto)`` — mesmo texto do mesmo número em <30s
   (retry com id novo ou duplo webhook). Só vale para texto não-vazio;
   áudios e botões passam direto.

TTL de 24h e teto de 5000 ids (poda por idade, nunca limpa tudo de uma vez).
Escritas atômicas (tmp + replace) e best-effort: falhar nunca quebra o reply.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

TTL_SECONDS = 24 * 3600
MAX_IDS = 5000
SAME_TEXT_WINDOW_SECONDS = 30


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


class Dedupe:
    def __init__(self, path: Path | str | None = None) -> None:
        self._lock = threading.Lock()
        self._path = Path(path) if path else None
        self._seen: dict[str, float] = {}
        self._last_text: dict[str, list] = {}
        self._load()

    def _load(self) -> None:
        if not self._path:
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        now = time.time()
        try:
            for mid, ts in (data.get("seen") or {}).items():
                if isinstance(ts, (int, float)) and now - ts < TTL_SECONDS:
                    self._seen[str(mid)] = float(ts)
            for phone, item in (data.get("last_text") or {}).items():
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    self._last_text[str(phone)] = [str(item[0]), float(item[1])]
        except (AttributeError, TypeError, ValueError):
            return

    def _save(self) -> None:
        if not self._path:
            return
        try:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"seen": self._seen, "last_text": self._last_text},
                           ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(tmp, self._path)
        except OSError:
            pass

    def _prune(self, now: float) -> None:
        if len(self._seen) > MAX_IDS:
            cutoff = now - TTL_SECONDS
            self._seen = {k: v for k, v in self._seen.items() if v >= cutoff}
            if len(self._seen) > MAX_IDS:
                keep = sorted(self._seen.items(), key=lambda kv: kv[1])[-MAX_IDS:]
                self._seen = dict(keep)

    def already_seen(self, msg_id: str, now: float | None = None) -> bool:
        """True se este id já foi processado (duplicata). Registra ids novos."""
        if not msg_id:
            return False
        now = time.time() if now is None else now
        with self._lock:
            prev = self._seen.get(msg_id)
            if prev is not None and now - prev < TTL_SECONDS:
                return True
            self._seen[msg_id] = now
            self._prune(now)
            self._save()
            return False

    def same_text_recent(
        self,
        phone: str,
        text: str,
        now: float | None = None,
        window: float = SAME_TEXT_WINDOW_SECONDS,
    ) -> bool:
        """True se o mesmo número mandou o mesmo texto há <window segundos.

        Registra a ocorrência atual em todo caso. Texto vazio nunca duplica
        (áudio/mídia passam direto).
        """
        norm = _norm_text(text)
        if not phone or not norm:
            return False
        now = time.time() if now is None else now
        with self._lock:
            prev = self._last_text.get(str(phone))
            dup = bool(prev and prev[0] == norm and 0 <= now - prev[1] < window)
            self._last_text[str(phone)] = [norm, now]
            self._save()
            return dup
