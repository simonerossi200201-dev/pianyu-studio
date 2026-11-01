# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Lock one language for a stitch batch, then refuse a tier we cannot align."""
from __future__ import annotations

import re

from cutora.asr.models import align_model_label, align_tier, language_capability
from cutora.asr.languages import align_phrase


# --- Copy-First Language Detection (文案优先语种检测) ---
# 视频是根据文案生成的，因此当识别语言设为 auto 时，必须优先从原文案判定语种，
# 绝不能被某个生成出错（例如法语文案被模型误读成英语）的单条视频带偏整批识别语言！

_SCRIPT_LANG_STOPWORDS: dict[str, frozenset[str]] = {
    "fr": frozenset({
        "je", "tu", "il", "elle", "nous", "vous", "ils", "elles", "le", "la", "les",
        "un", "une", "des", "du", "de", "dans", "sur", "avec", "pour", "par", "pas",
        "ne", "plus", "mais", "ou", "et", "donc", "car", "ni", "que", "qui", "quoi",
        "dont", "mon", "ton", "son", "ma", "ta", "sa", "mes", "tes", "ses", "notre",
        "votre", "leur", "leurs", "ce", "cet", "cette", "ces", "seigneur", "dieu",
        "priere", "prière", "paix", "coeur", "cœur", "foi", "vie", "esprit", "amen",
        "merci", "pere", "père", "jesus", "jésus", "christ", "benis", "bénis", "prie",
        "lorsque", "comme", "tout", "tous", "toute", "toutes", "meme", "même", "sans",
        "sous", "vers", "chez", "tres", "très", "bien", "mal", "peu", "beaucoup",
        "aujourdhui", "toujours", "jamais", "aussi", "encore", "alors", "apres", "après",
        "avant", "depuis", "pendant", "contre", "entre", "chaque", "chacun", "quelqu",
    }),
    "es": frozenset({
        "el", "la", "los", "las", "un", "una", "unos", "unas", "yo", "tu", "tú", "el",
        "él", "ella", "nosotros", "vosotros", "ellos", "ellas", "usted", "ustedes",
        "mi", "mis", "tu", "tus", "su", "sus", "nuestro", "nuestra", "este", "esta",
        "estos", "estas", "ese", "esa", "aquel", "que", "qué", "quien", "quién",
        "como", "cómo", "cuando", "cuándo", "donde", "dónde", "porque", "para", "por",
        "con", "sin", "sobre", "entre", "hasta", "desde", "hacia", "pero", "mas", "más",
        "sino", "tambien", "también", "siempre", "nunca", "todo", "todos", "toda",
        "todas", "vida", "dios", "señor", "senor", "oracion", "oración", "fe", "paz",
        "corazon", "corazón", "padre", "hijo", "espiritu", "espíritu", "santo", "bendice",
    }),
    "pt": frozenset({
        "o", "a", "os", "as", "um", "uma", "uns", "umas", "eu", "tu", "ele", "ela",
        "nos", "nós", "vos", "vós", "eles", "elas", "voce", "você", "voces", "vocês",
        "meu", "minha", "meus", "minhas", "teu", "tua", "seu", "sua", "seus", "suas",
        "nosso", "nossa", "este", "esta", "isto", "esse", "essa", "isso", "aquele",
        "aquela", "que", "quem", "qual", "como", "quando", "onde", "porque", "para",
        "por", "com", "sem", "sobre", "entre", "ate", "até", "desde", "mas", "mais",
        "tambem", "também", "sempre", "nunca", "tudo", "todos", "todas", "muito",
        "vida", "deus", "senhor", "oracao", "oração", "fe", "fé", "paz", "coracao",
        "coração", "pai", "filho", "espirito", "espírito", "abençoe", "abencoe", "nao", "não",
    }),
    "it": frozenset({
        "il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "io", "tu", "lui", "lei",
        "noi", "voi", "loro", "mio", "mia", "miei", "mie", "tuo", "tua", "suo", "sua",
        "nostro", "nostra", "vostro", "vostra", "questo", "questa", "questi", "queste",
        "quello", "quella", "che", "chi", "cui", "come", "quando", "dove", "perche",
        "perché", "per", "con", "su", "tra", "fra", "da", "nel", "nella", "dei", "delle",
        "del", "della", "non", "piu", "più", "ma", "anche", "sempre", "mai", "tutto",
        "tutti", "tutte", "molto", "vita", "dio", "signore", "preghiera", "fede", "pace",
        "cuore", "padre", "figlio", "spirito", "benedici", "grazie", "sono", "sei",
    }),
    "de": frozenset({
        "ich", "du", "er", "sie", "es", "wir", "ihr", "der", "die", "das", "den", "dem",
        "des", "ein", "eine", "einen", "einem", "einer", "eines", "mein", "meine", "dein",
        "deine", "sein", "seine", "ihr", "ihre", "unser", "unsere", "dieser", "diese",
        "dieses", "und", "oder", "aber", "denn", "sondern", "weil", "dass", "wenn", "als",
        "wie", "wo", "wann", "warum", "wer", "was", "nicht", "kein", "keine", "mit", "von",
        "zu", "in", "im", "auf", "für", "fuer", "über", "ueber", "unter", "vor", "nach",
        "bei", "durch", "gegen", "ohne", "um", "gott", "herr", "gebet", "frieden", "herz",
        "glaube", "leben", "vater", "geist", "segne", "immer", "nie", "alles", "alle",
    }),
    "en": frozenset({
        "the", "and", "that", "have", "for", "not", "with", "you", "this", "but", "his",
        "from", "they", "say", "her", "she", "will", "one", "all", "would", "there",
        "their", "what", "out", "about", "who", "get", "which", "when", "make", "can",
        "like", "time", "just", "him", "know", "take", "people", "into", "year", "your",
        "good", "some", "could", "them", "see", "other", "than", "then", "now", "look",
        "only", "come", "its", "over", "think", "also", "back", "after", "use", "two",
        "how", "our", "work", "first", "well", "way", "even", "new", "want", "because",
        "any", "these", "give", "day", "most", "lord", "god", "prayer", "peace", "heart",
        "faith", "life", "father", "jesus", "holy", "spirit", "bless", "believe", "pray",
    }),
    "id": frozenset({
        "yang", "dan", "di", "ini", "itu", "dengan", "untuk", "tidak", "dari", "dalam",
        "akan", "pada", "juga", "saya", "aku", "kamu", "engkau", "dia", "mereka", "kita",
        "kami", "ada", "bisa", "atau", "ke", "karena", "oleh", "sudah", "telah", "saat",
        "jika", "kalau", "lebih", "semua", "orang", "tuhan", "yesus", "doa", "hati",
        "hidup", "damai", "iman", "berkat", "bapa", "kasih", "ampun",
    }),
    "ms": frozenset({
        "yang", "dan", "di", "ini", "itu", "dengan", "untuk", "tidak", "dari", "dalam",
        "akan", "pada", "juga", "saya", "awak", "engkau", "dia", "mereka", "kita", "kami",
        "boleh", "kerana", "oleh", "sudah", "telah", "apabila", "jika", "semua", "tuhan",
    }),
    "sw": frozenset({
        "na", "ya", "wa", "kwa", "katika", "ni", "si", "la", "za", "vya", "mungu",
        "bwana", "yesu", "sala", "maombi", "amani", "moyo", "imani", "maisha", "baba",
        "roho", "mtakatifu", "bariki", "wewe", "mimi", "yeye", "sisi", "wao", "hii",
        "huu", "hizi", "kama", "lakini", "pia", "sana", "wote", "kila", "kwamba", "ili",
    }),
    "mg": frozenset({
        "ny", "sy", "ary", "dia", "ho", "amin", "izay", "izao", "ity", "ireo", "izy",
        "aho", "anao", "ianao", "isika", "izahay", "andriamanitra", "tompo", "jesosy",
        "vavaka", "fiadanana", "fo", "finoana", "fiainana", "ray", "fanahy", "masina",
        "fitahiana", "mba", "fa", "nefa", "koa", "rehetra", "tsirairay", "noho", "satria",
    }),
    "nl": frozenset({
        "de", "het", "een", "en", "van", "in", "is", "op", "te", "dat", "die", "voor",
        "met", "als", "zijn", "maar", "om", "aan", "ook", "dan", "of", "wat", "bij",
        "tot", "uit", "naar", "door", "niet", "geen", "ik", "jij", "hij", "zij", "wij",
        "jullie", "mijn", "jouw", "heer", "god", "gebed", "vrede", "hart", "geloof",
    }),
    "pl": frozenset({
        "i", "w", "nie", "na", "z", "do", "to", "że", "ze", "o", "jak", "ale", "po",
        "co", "tak", "za", "od", "przez", "dla", "czy", "jest", "są", "być", "mój",
        "twój", "jego", "jej", "nasz", "pan", "bóg", "bog", "modlitwa", "pokój", "serce",
    }),
    "tr": frozenset({
        "ve", "bir", "bu", "da", "de", "için", "icin", "ile", "o", "ne", "gibi", "daha",
        "çok", "cok", "ama", "veya", "her", "kadar", "sonra", "olarak", "ben", "sen",
        "biz", "siz", "onlar", "benim", "senin", "tanrı", "tanri", "rab", "dua", "huzur",
    }),
    "vi": frozenset({
        "và", "của", "là", "có", "trong", "cho", "không", "với", "được", "những", "các",
        "một", "người", "này", "đã", "khi", "để", "như", "từ", "tôi", "bạn", "chúa",
        "cầu", "nguyện", "bình", "an", "trái", "tim", "đức", "tin", "cuộc", "sống",
    }),
}


def detect_language_from_text(text: str) -> tuple[str | None, float]:
    """Detect ISO-639-1 language code from copy text with high accuracy.
    Returns (code, confidence) or (None, 0.0) if text is empty/indeterminate.
    """
    raw = (text or "").strip()
    if not raw:
        return None, 0.0

    # 1. Non-Latin script character counting
    n_han = len(re.findall(r"[\u4e00-\u9fff]", raw))
    n_kana = len(re.findall(r"[\u3040-\u30ff]", raw))
    n_hangul = len(re.findall(r"[\uac00-\ud7af\u1100-\u11ff]", raw))
    n_arabic = len(re.findall(r"[\u0600-\u06ff\u0750-\u077f]", raw))
    n_cyrillic = len(re.findall(r"[\u0400-\u04ff]", raw))
    n_greek = len(re.findall(r"[\u0370-\u03ff]", raw))
    n_hebrew = len(re.findall(r"[\u0590-\u05ff]", raw))
    n_thai = len(re.findall(r"[\u0e00-\u0e7f]", raw))
    n_deva = len(re.findall(r"[\u0900-\u097f]", raw))

    total_alpha = max(1, len(re.findall(r"\w", raw, flags=re.UNICODE)))
    if n_kana >= 2 and (n_kana + n_han) / total_alpha > 0.2:
        return "ja", 0.99
    if n_hangul >= 2 and n_hangul / total_alpha > 0.25:
        return "ko", 0.99
    if n_han >= 3 and n_han / total_alpha > 0.25:
        return "zh", 0.99
    if n_arabic >= 3 and n_arabic / total_alpha > 0.25:
        return "ar", 0.99
    if n_hebrew >= 3 and n_hebrew / total_alpha > 0.25:
        return "he", 0.99
    if n_thai >= 3 and n_thai / total_alpha > 0.25:
        return "th", 0.99
    if n_deva >= 3 and n_deva / total_alpha > 0.25:
        return "hi", 0.99
    if n_greek >= 3 and n_greek / total_alpha > 0.25:
        return "el", 0.99
    if n_cyrillic >= 3 and n_cyrillic / total_alpha > 0.25:
        if re.search(r"[іїєґІЇЄҐ]", raw):
            return "uk", 0.98
        return "ru", 0.98

    # 2. Latin-script languages: score stopwords + distinctive diacritics/patterns
    low = raw.lower()
    # Keep accented characters for word matching
    words = re.findall(r"[a-zàâäéèêëîïôöùûüÿçœæáíóúñãõßąćęłńśźżğıöşüđăâêôơưàảãáạ]+", low)
    if not words:
        return None, 0.0

    scores: dict[str, float] = {code: 0.0 for code in _SCRIPT_LANG_STOPWORDS}
    n_w = len(words)
    for w in words:
        for code, sw_set in _SCRIPT_LANG_STOPWORDS.items():
            if w in sw_set:
                scores[code] += 1.0

    # Distinctive orthographic / diacritic bonuses
    if re.search(r"[œæ]|(?:\b(?:c'est|qu'il|qu'un|qu'une|d'un|d'une|l'on|n'est|j'ai|jusqu'au|auprès)\b)|[«»]", low):
        scores["fr"] += 3.5
    if re.search(r"[éèêàùçîôû]", low):
        scores["fr"] += 1.5
    if re.search(r"[ñ¿¡]", low):
        scores["es"] += 4.0
    if re.search(r"[ãõ]|(?:\b(?:não|oração|coração|você|também)\b)", low):
        scores["pt"] += 4.0
    if re.search(r"[äöüß]", low):
        scores["de"] += 3.5
    if re.search(r"\b(?:della|nella|degli|delle|perché|più|preghiera|signore)\b", low):
        scores["it"] += 3.0
    if re.search(r"[ąćęłńśźż]", low):
        scores["pl"] += 4.0
    if re.search(r"[ğış]", low):
        scores["tr"] += 4.0
    if re.search(r"[đăơưảạểễệỉịỏọổỗộởỡợủụửữựỷỵ]", low):
        scores["vi"] += 4.5

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_code, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    if best_score <= 0.0:
        return None, 0.0

    hit_ratio = best_score / max(1.0, float(n_w))
    margin = (best_score - second_score) / max(1.0, best_score)
    conf = min(0.99, max(0.65, 0.70 + 0.20 * min(1.0, hit_ratio * 2.5) + 0.10 * margin))
    if best_score >= 2.0 or (n_w <= 6 and best_score >= 1.0):
        return best_code, round(conf, 3)
    return None, 0.0


def detect_language_from_scripts(scripts: dict[str, str] | None) -> tuple[str | None, float]:
    """Aggregate copy texts across all groups in a stitch batch and detect the primary language."""
    if not scripts:
        return None, 0.0
    combined = "\n".join(str(v or "").strip() for v in scripts.values() if str(v or "").strip())
    if not combined.strip():
        return None, 0.0
    return detect_language_from_text(combined)


class StitchLanguageError(RuntimeError):
    """Raised before any cut when the batch language cannot be aligned."""


def read_whisper_info(info) -> tuple[str, float]:
    """``info.language`` and ``info.language_probability`` from faster-whisper."""
    lang = getattr(info, "language", None)
    prob = getattr(info, "language_probability", None)
    if lang is None and isinstance(info, dict):
        lang = info.get("language")
        prob = info.get("language_probability")
    if prob is None:
        prob = 0.0
    try:
        conf = float(prob)
    except (TypeError, ValueError):
        conf = 0.0
    return str(lang or "").strip().lower(), conf


def read_whisper_detection(model, audio_or_path):
    """Call faster-whisper ``transcribe(language=None)`` and return its info.

    A whisperx pipeline is unwrapped via ``.model``. A test double may
    implement ``transcribe`` and return ``(segments, info)``. Segments are
    not iterated: ``info`` is filled before the generator runs.
    """
    fw = model
    inner = getattr(model, "model", None)
    if inner is not None and hasattr(inner, "transcribe"):
        fw = inner
    result = fw.transcribe(audio_or_path, language=None)
    if isinstance(result, tuple) and len(result) >= 2:
        return result[1]
    raise StitchLanguageError("识别模型没有返回语言检测结果，拼接没有开始。")


def detection_log(code: str, prob: float) -> str:
    cap = language_capability(code) if code else {"name_zh": code or "未知", "code": code}
    name = cap.get("name_zh") or code or "未知"
    shown = code or "未知"
    return "检测到%s（%s），置信度 %.0f%%。" % (name, shown, float(prob) * 100.0)


def align_choice_log(code: str) -> str:
    cap = language_capability(code)
    tier = str(cap.get("align_tier") or "none")
    return "对齐档位：%s（%s），模型 %s。" % (
        align_phrase(tier), tier, align_model_label(code))


def unstitchable_message(code: str) -> str:
    cap = language_capability(code)
    name = cap.get("name_zh") or code or "这种语言"
    shown = code or "?"
    return (
        "检测到%s（%s）。这种语言能把声音转成文字，但没有逐词时间对齐，"
        "拼接不知道每个词从哪一秒开始，所以还不能拼。"
        "请换一种标着「逐词精确」或「可用（精度略低）」的语言。"
        % (name, shown)
    )


def resolve_detected_language(info, confirm_fn) -> str:
    """Lock one code for the whole batch.

    ``confirm_fn`` is called only when the probability is below the detect
    threshold. It returns the code to use, or None if the user cancelled.
    A ``none`` align tier raises before any cut.
    """
    from cutora.asr.detect import build_detection_result, get_detect_threshold
    lang, prob = read_whisper_info(info)
    if prob < get_detect_threshold():
        det = build_detection_result(lang, prob)
        chosen = confirm_fn(det)
        if not chosen:
            raise StitchLanguageError("已取消：没有确认检测到的语言，拼接没有开始。")
        lang = str(chosen).strip().lower()
    if not lang:
        raise StitchLanguageError(
            "没有从这段声音里认出语言，拼接没有开始。"
            "请在语言里改选一种标着「逐词精确」或「可用（精度略低）」的。")
    if align_tier(lang) == "none":
        raise StitchLanguageError(unstitchable_message(lang))
    return lang
