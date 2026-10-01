"""TTS: texto curto -> ogg/opus (mensagem de voz). edge-tts + ffmpeg, cache por hash."""
import asyncio
import hashlib
import os
import subprocess
import tempfile
import threading

VOICE = os.environ.get("TTS_VOICE", "pt-BR-AntonioNeural")
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "tts_cache")


def _synthesize_mp3(texto: str, mp3_path: str) -> None:
    import edge_tts

    def _run() -> None:
        asyncio.run(edge_tts.Communicate(texto, VOICE).save(mp3_path))

    try:
        asyncio.get_running_loop()
        # Há um event loop ativo (handler async do FastAPI): roda a síntese em thread própria.
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=90)
    except RuntimeError:
        _run()


def tts_ogg(texto: str) -> str | None:
    """Gera (ou reaproveita do cache) o .ogg da fala. Retorna o caminho ou None."""
    texto = (texto or "").strip()
    if not texto or len(texto) > 600:
        return None
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        h = hashlib.sha256((VOICE + "|" + texto).encode()).hexdigest()[:16]
        out = os.path.join(CACHE_DIR, h + ".ogg")
        if os.path.exists(out):
            return out
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            mp3 = f.name
        try:
            _synthesize_mp3(texto, mp3)
            r = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", mp3,
                 "-c:a", "libopus", "-b:a", "32k", out],
                capture_output=True, timeout=60,
            )
            if r.returncode != 0 or not os.path.exists(out):
                return None
            return out
        finally:
            if os.path.exists(mp3):
                os.unlink(mp3)
    except Exception as e:
        print(f"[tts] falhou: {e}", flush=True)
        return None
