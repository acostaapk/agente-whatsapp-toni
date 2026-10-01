"""STT: áudio do cliente (ogg) -> texto. faster-whisper small, CPU, português."""
import os
import re
import tempfile
import urllib.request

MODEL_NAME = os.environ.get("WHISPER_MODEL", "small")
MAX_BYTES = 5_000_000  # recusa áudio >5MB

def _patch_av() -> None:
    """faster-whisper 1.2.1 passa metadata_errors ao av.open; av>=15 removeu o kwarg."""
    try:
        import av
        if getattr(av.open, "_ton_patched", False):
            return
        _orig = av.open

        def _open(*a, **k):
            k.pop("metadata_errors", None)
            return _orig(*a, **k)

        _open._ton_patched = True
        av.open = _open
    except Exception as e:
        print(f"[stt] patch av falhou: {e}", flush=True)


_patch_av()

_MODEL = None


def _get_model():
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel
        _MODEL = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
    return _MODEL


INITIAL_PROMPT = ("Maquininha Ton, T1, T2, T3, T3 Smart, TapTon, Pix, débito, crédito à vista, "
                  "parcelado, adesão, quero a T3, taxa, entrega, desconto.")


def _fix_models(texto: str) -> str:
    """Corrige erros clássicos de transcrição de modelo (sem tocar em '3 mil' de volume)."""
    if "mil" in (texto or "").lower():
        return texto
    texto = re.sub(r"\bat[ée]\s*3\b|\bt[eê]\s*tr[eê]s\b|\ba\s+t[eê]s\b", "T3", texto or "", flags=re.IGNORECASE)
    return texto


def _transcribe(path: str) -> str:
    segments, _ = _get_model().transcribe(
        path, language="pt", beam_size=5,
        initial_prompt=INITIAL_PROMPT, condition_on_previous_text=False,
    )
    return _fix_models(" ".join(s.text for s in segments).strip())


def transcribe_bytes(data: bytes) -> str:
    """Transcreve bytes de áudio (já decifrados). Retorna '' se falhar."""
    try:
        if not data or len(data) > MAX_BYTES:
            return ""
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(data)
            path = f.name
        try:
            return _transcribe(path)
        finally:
            os.unlink(path)
    except Exception as e:
        print(f"[stt] falhou: {e} (bytes={len(data) if data else 0})", flush=True)
        return ""


def transcribe_audio_url(url: str) -> str:
    """Baixa o áudio e transcreve. Retorna '' se falhar."""
    try:
        req = urllib.request.Request(url or "", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        print(f"[stt] baixados {len(data)} bytes de url; magic={data[:12].hex()}", flush=True)
        return transcribe_bytes(data)
    except Exception as e:
        print(f"[stt] download falhou: {e}", flush=True)
        return ""
