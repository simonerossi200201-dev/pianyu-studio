"""Worker for Edge Neural TTS (runs in D:\\0_Dev\\AudioClone\\.venv_cloner).
Keeps warm, streams neural audio with realistic human voices. 0 MB VRAM, free.

macro_polyglot (default):
  - Everything (Chinese + American English) -> Ava  (en-US-AvaMultilingualNeural)
  - Any Italian span -> Isabella (it-IT-IsabellaNeural, native Italian: pure vowels, rolled r, geminates)
    Detection works on whole sentences AND on Italian words/phrases embedded inside Chinese
    sentences (e.g. "意大利默认的 caffè 就是浓缩咖啡").
  - Seamless stitching: each clip's edge silence is trimmed, loudness is matched between the
    two voices, and the pause inserted at each switch depends on the punctuation there
    (inline word ~60 ms, comma ~150 ms, sentence end ~300 ms), with micro fades against clicks.
Direct mode: any single Edge voice id.
Emoji / markdown are stripped so it never reads "灯泡", "旗帜", "星号".
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import time

try:
    import edge_tts
except ImportError:
    edge_tts = None

try:
    import numpy as np
except ImportError:
    np = None

VOICE_MAIN = os.environ.get("ROBOTA_VOICE_MAIN", "en-US-AvaMultilingualNeural")  # Chinese + American English
VOICE_IT = os.environ.get("ROBOTA_VOICE_IT", "it-IT-IsabellaNeural")             # native Italian

FFMPEG_CANDIDATES = [r"D:\0_Dev\CUTORA\runtime\ffmpeg\ffmpeg.exe", "ffmpeg"]
SR = 24000
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ---------------------------------------------------------------- Italian / English lexicons
IT_STRONG = {
    "caffè", "caffe", "cornetto", "cornetti", "favore", "prendo", "grazie", "vorrei", "cappuccino",
    "macchiato", "buongiorno", "buonasera", "buonanotte", "arrivederci", "prego", "sono", "siamo",
    "siete", "bello", "bella", "buono", "buona", "vuoto", "ripieno", "caldo", "freddo", "conto",
    "tavolo", "della", "delle", "degli", "dello", "nella", "nelle", "negli", "sulla", "questo",
    "questa", "questi", "queste", "quello", "quella", "anche", "sempre", "bene", "molto", "tanto",
    "poco", "ciao", "scusa", "scusi", "piacere", "cosa", "quando", "perché", "perche", "quanto",
    "acqua", "pane", "sì", "allora", "adesso", "oggi", "domani", "ieri", "ragazzo",
    "ragazza", "amico", "amica", "dolce", "gelato", "birra", "bicchiere", "posso",
    "vuoi", "voglio", "dove", "andiamo", "mangiare", "bere", "parlare", "capisco", "non", "ecco",
    "magari", "certo", "dai", "niente", "tutto", "tutti", "ancora", "subito", "insieme", "lavoro",
    "casa", "piazza", "strada", "treno", "stazione", "biglietto", "colazione", "pranzo", "cena",
    "spremuta", "aperitivo", "spritz", "tramezzino", "panino", "pasticceria", "zucchero",
    "latte", "orzo", "decaffeinato", "lungo", "ristretto", "corretto", "brioche",
    "risultato", "finale", "dipende", "dipendere", "prove", "prova", "causa", "guasto", "decisione",
    "giudice", "risposta", "scadenza", "linea", "comitato", "regionale", "comunicazioni", "dalle",
    "dalla", "dallo", "dagli", "alle", "allo", "agli", "sul", "sui", "nel", "nei", "col", "essere",
    "avere", "fare", "dire", "andare", "sapere", "volere", "potere", "dovere", "grazie mille",
}
IT_WEAK = {
    "un", "una", "uno", "il", "lo", "la", "gli", "le", "del", "dei", "al", "alla", "e", "è", "di",
    "da", "in", "con", "su", "per", "tra", "fra", "che", "mi", "ti", "ci", "si", "vi", "ho", "hai",
    "ha", "come", "chi", "pizza", "pasta", "vino", "espresso", "ma", "o", "io", "tu", "lui", "lei",
    "noi", "voi", "loro", "mio", "mia", "tuo", "tua", "bar",
}
EN_WORDS = {
    "the", "a", "an", "and", "i", "you", "he", "she", "we", "they", "it", "can", "could", "get",
    "please", "is", "are", "was", "were", "have", "has", "will", "would", "just", "of", "to", "this",
    "that", "what", "how", "my", "your", "with", "for", "do", "does", "don't", "i'll", "i'm", "me",
    "coffee", "like", "want", "thank", "thanks", "hello", "hi", "good", "morning", "yes", "no",
    "be", "at", "on", "from", "by", "or", "but", "not", "so", "if", "there", "here", "one", "some",
    "croissant", "take", "go", "let's", "it's", "that's", "okay", "ok", "sure", "great",
    "nice", "see", "say", "said", "note", "tip", "example", "english", "italian", "version",
}
IT_SUFFIX = re.compile(r"(zione|zioni|mente|etto|etta|etti|ette|ino|ina|ini|ine|are|ere|ire|ato|ata|"
                       r"ati|ate|iamo|ete|issimo|issima|ello|ella|ucci|accio|eria)$")
IT_GEMINATE = re.compile(r"(bb|cc|dd|ff|gg|ll|mm|nn|pp|rr|ss|tt|vv|zz)[aeiouàèéìòù]$")
WORD_RE = re.compile(r"[a-zà-öø-ÿ]+(?:['’][a-zà-öø-ÿ]+)?")
LATIN_RUN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]*"
                       r"(?:[ \t,;:!?¿¡'’\-+=/&]+[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]*)*[.!?…]*")
CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
SPOKEN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ\u3400-\u9fff0-9]")


def list_voices():
    return [
        {"id": "macro_polyglot", "lang": "multilingual", "label": "🌐 Ava 中英 + Isabella 意大利语母语 (推荐)"},
        {"id": "en-US-AvaMultilingualNeural", "lang": "multilingual", "label": "✨ Ava (中英多语言自然人声)"},
        {"id": "it-IT-IsabellaNeural", "lang": "it-IT", "label": "🇮🇹 Isabella (意大利语纯正母语·真弹舌)"},
        {"id": "en-US-JennyNeural", "lang": "en-US", "label": "🇺🇸 Jenny (美式英语纯正母语)"},
        {"id": "zh-CN-XiaoxiaoNeural", "lang": "zh-CN", "label": "🇨🇳 晓晓 (中文标准人声)"},
    ]


def clean_speech_text(text: str) -> str:
    """Clean markdown, symbols, and emojis so TTS only reads natural human speech."""
    if not text:
        return ""
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Remove emojis and flag symbols completely
    text = re.sub(r"[\U00010000-\U0010ffff]", "", text)
    text = re.sub(r"[\u2600-\u27bf\u2300-\u23ff\u2b50\u200d\ufe0f]", "", text)
    # Remove horizontal dividers and list bullet markers
    text = re.sub(r"^[-=_]{2,}\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*(?:[#>*\-+]|\d+[.)])+\s*", "", text, flags=re.MULTILINE)
    # Remove markdown bold/italic/code/link syntax
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^[>\s]+", "", text, flags=re.MULTILINE)
    # Strip standalone formatting leftovers like **, *, _, ~
    text = re.sub(r"[*_~#]+", "", text)
    # Quotes / brackets are not spoken; dropping them keeps voice-switch boundaries clean
    text = re.sub(r"[\"“”«»「」『』]", "", text)
    text = re.sub(r"\s*[（(]\s*", " ", text)
    text = re.sub(r"\s*[）)]\s*", " ", text)
    text = re.sub(r" +([。，！？、；：.,!?;:])", r"\1", text)
    # Clean multiple dashes/colons
    text = re.sub(r"[：:]{2,}", "：", text)
    text = re.sub(r"[-—]{2,}", "，", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines).strip()


# ---------------------------------------------------------------- language detection
def italian_score(run: str) -> tuple[float, float]:
    low = run.lower()
    words = WORD_RE.findall(low)
    it = en = 0.0
    if re.search(r"[àèìòùé]", low):
        it += 3
    for w in words:
        if w in IT_STRONG:
            it += 2
        elif w in IT_WEAK:
            it += 0.6
        if w in EN_WORDS:
            en += 2
        if len(w) >= 4 and (IT_SUFFIX.search(w) or IT_GEMINATE.search(w)):
            it += 1
        if re.search(r"gli|gn[aeiou]|zz|cch|sci", w):
            it += 0.5
        if re.search(r"[wkyjx]|th|sh|ck|ee|oo|ing$|ly$|tion$|ght", w):
            en += 1
        elif len(w) >= 3 and w[-1] in "aeiouàèéìòù":
            it += 0.4  # Italian words almost always end in a vowel
    if "per favore" in low:
        it += 2
    return it, en


def is_italian_run(run: str, bias: bool) -> bool:
    if not WORD_RE.search(run.lower()):
        return False
    it, en = italian_score(run)
    if it >= 2 and it > en:
        return True
    # In an Italian-learning reply, any Latin word/phrase that is not clearly English is Italian
    return bias and en < 2 and it >= en


def _ensure_end(line: str) -> str:
    """Give each line a terminal mark so the TTS phrases it as a sentence (natural pause)."""
    if re.search(r"[。！？!?.…:：;；,，]$", line):
        return line
    return line + ("。" if CJK.search(line) else ".")


def _doc_is_italian(text: str) -> bool:
    for m in LATIN_RUN.finditer(clean_speech_text(text)):
        it, en = italian_score(m.group(0))
        if it >= 2.5 and it > en:
            return True
    return False


def split_segments(text: str, context_lang: str = "auto") -> list[tuple[str, str]]:
    """Return [(text, 'it'|'main')], merging neighbours with the same voice."""
    ctx_it = (context_lang or "").lower().startswith(("it", "意")) or _doc_is_italian(text)
    segs: list[list[str]] = []

    def push(t: str, v: str):
        t = t.strip()
        if not t:
            return
        if not SPOKEN.search(t):  # punctuation-only fragment -> glue to previous
            if segs:
                segs[-1][0] = segs[-1][0].rstrip() + t
            return
        if segs and segs[-1][1] == v:
            prev = segs[-1][0]
            if prev.endswith("\n") or CJK.search(prev.rstrip()[-1:]) or CJK.search(t[:1]):
                segs[-1][0] = prev.rstrip(" ") + t
            else:
                segs[-1][0] = prev.rstrip() + " " + t
        else:
            segs.append([t, v])

    section = None  # None | "it" | "en"  (from markdown headers)
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        if raw.startswith("#"):
            if re.search(r"意大利|Italian|Italiano|意语", raw, re.I):
                section = "it"
            elif re.search(r"英语|English|美语|英文", raw, re.I):
                section = "en"
            else:
                section = None
        line = clean_speech_text(raw)
        if not line:
            continue
        line = _ensure_end(line)
        bias = section == "it" or (section is None and ctx_it)
        pos = 0
        for m in LATIN_RUN.finditer(line):
            run = m.group(0)
            if is_italian_run(run, bias):
                push(line[pos:m.start()], "main")
                push(run, "it")
                pos = m.end()
        push(line[pos:], "main")
        if segs:
            segs[-1][0] += "\n"

    return [(t.strip(), v) for t, v in segs if SPOKEN.search(t)]


# ---------------------------------------------------------------- synthesis + seamless stitching
_CACHE: "dict[tuple, bytes]" = {}
_CACHE_MAX = 400


async def get_audio_for_block(text: str, voice: str, rate: str, pitch: str) -> bytes:
    key = (text, voice, rate, pitch)
    if key in _CACHE:
        _CACHE[key] = _CACHE.pop(key)  # refresh LRU position
        return _CACHE[key]
    async def _once() -> bytes:
        comm = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
        chunks = []
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)

    for attempt in range(3):
        try:
            data = await asyncio.wait_for(_once(), timeout=6 + len(text) / 25)
            if data and len(text) <= 300:
                _CACHE[key] = data
                while len(_CACHE) > _CACHE_MAX:
                    _CACHE.pop(next(iter(_CACHE)))
            return data
        except Exception as e:
            print(f"Error synthesizing with {voice} (try {attempt + 1}): {e!r}", file=sys.stderr)
            await asyncio.sleep(0.2)
    return b""


def _ffmpeg(args: list[str], data: bytes) -> bytes | None:
    for ff in FFMPEG_CANDIDATES:
        try:
            r = subprocess.run([ff, "-hide_banner", "-loglevel", "error", *args], input=data,
                               capture_output=True, timeout=120, creationflags=NOWIN)
            if r.returncode == 0 and r.stdout:
                return r.stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


def _decode(mp3: bytes):
    pcm = _ffmpeg(["-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", str(SR), "pipe:1"], mp3)
    if pcm is None:
        return None
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def _trim(x, thr_db: float = -45.0, pad_ms: int = 25):
    win = int(SR * 0.01)
    if len(x) < win * 3:
        return x
    n = len(x) // win
    rms = np.sqrt(np.mean(x[: n * win].reshape(n, win) ** 2, axis=1) + 1e-12)
    loud = np.where(20 * np.log10(rms) > thr_db)[0]
    if not len(loud):
        return x
    pad = int(SR * pad_ms / 1000)
    return x[max(0, loud[0] * win - pad): min(len(x), (loud[-1] + 1) * win + pad)]


def _level(x, target_db: float = -19.0):
    win = int(SR * 0.02)
    n = len(x) // win
    if n < 2:
        return x
    rms = np.sqrt(np.mean(x[: n * win].reshape(n, win) ** 2, axis=1) + 1e-12)
    voiced = rms[20 * np.log10(rms) > -40]
    if not len(voiced):
        return x
    cur = 20 * np.log10(np.sqrt(np.mean(voiced ** 2)))
    gain = 10 ** (max(-6.0, min(6.0, target_db - cur)) / 20)
    return x * gain


def _fade(x, ms: int = 8):
    k = min(len(x) // 2, int(SR * ms / 1000))
    if k > 0:
        ramp = np.linspace(0.0, 1.0, k, dtype=np.float32)
        x = x.copy()
        x[:k] *= ramp
        x[-k:] *= ramp[::-1]
    return x


def _gap_after(text: str) -> float:
    t = text.rstrip()
    if not t or re.search(r"[。！？!?…]$", t) or (t.endswith(".") and not t.endswith("..")):
        return 0.30
    if re.search(r"[，,、；;：:]$", t):
        return 0.15
    return 0.06  # inline switch inside a sentence: keep it tight


def stitch(parts: list[bytes], texts: list[str]) -> bytes:
    if np is None:
        return b"".join(parts)
    clips = []
    for p in parts:
        if not p:
            clips.append(None)
            continue
        x = _decode(p)
        if x is None:
            return b"".join(parts)  # no ffmpeg -> raw concat fallback
        clips.append(_fade(_level(_trim(x))))
    out = []
    for i, c in enumerate(clips):
        if c is None:
            continue
        out.append(c)
        if i < len(clips) - 1:
            out.append(np.zeros(int(SR * _gap_after(texts[i])), dtype=np.float32))
    if not out:
        return b""
    y = np.clip(np.concatenate(out), -1.0, 1.0)
    pcm = (y * 32767).astype(np.int16).tobytes()
    mp3 = _ffmpeg(["-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "pipe:0",
                   "-c:a", "libmp3lame", "-b:a", "64k", "-f", "mp3", "pipe:1"], pcm)
    return mp3 or b"".join(parts)


async def synth_macro_polyglot(text: str, rate: str, pitch: str, context_lang: str = "auto") -> bytes:
    segs = split_segments(text, context_lang)
    if not segs:
        return b""
    sem = asyncio.Semaphore(6)

    async def one(t: str, v: str) -> bytes:
        async with sem:
            return await get_audio_for_block(t, VOICE_IT if v == "it" else VOICE_MAIN, rate, pitch)

    parts = await asyncio.gather(*(one(t, v) for t, v in segs))
    if len(parts) == 1:
        return parts[0]
    return await asyncio.to_thread(stitch, list(parts), [t for t, _ in segs])


async def synth(text: str, voice: str, out_path: str, rate: str = "+0%", pitch: str = "+0Hz", context_lang: str = "auto"):
    t0 = time.time()

    if not voice or voice in ("macro_polyglot", "auto", "default", "mixed"):
        full_audio = await synth_macro_polyglot(text, rate, pitch, context_lang)
    else:
        cleaned = clean_speech_text(text) or "好的"
        full_audio = await get_audio_for_block(cleaned, voice, rate, pitch)

    with open(out_path, "wb") as f:
        f.write(full_audio)

    dur = time.time() - t0
    return {"ok": True, "seconds": round(dur, 2), "bytes": len(full_audio), "voice": voice or "macro_polyglot"}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
    print(json.dumps({"ready": True, "engine": "edge_tts_macro_polyglot"}), flush=True)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            cmd = req.get("cmd")
            if cmd == "ping":
                print(json.dumps({"ok": True, "engine": "edge_tts_macro_polyglot"}), flush=True)
                continue
            if cmd == "voices":
                print(json.dumps({"ok": True, "voices": list_voices()}), flush=True)
                continue

            text = req.get("text", "").strip()
            voice = req.get("voice") or "macro_polyglot"
            out = req.get("out")
            rate = req.get("rate") or "+0%"
            pitch = req.get("pitch") or "+0Hz"
            context_lang = req.get("lang") or "auto"
            # progressive playback sends one chunk at a time + the whole reply as context
            if req.get("context") and _doc_is_italian(req["context"]):
                context_lang = "it"

            res = loop.run_until_complete(synth(text, voice, out, rate, pitch, context_lang))
            print(json.dumps(res, ensure_ascii=False), flush=True)
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
