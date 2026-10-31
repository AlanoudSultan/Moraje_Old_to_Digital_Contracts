"""Arabic text helpers. Normalization is for matching and search only; original text is never changed."""
from __future__ import annotations

import re
import unicodedata
from datetime import date

ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩"
EASTERN_INDIC = "۰۱۲۳۴۵۶۷۸۹"
_DIGIT_MAP = {ord(c): str(i) for i, c in enumerate(ARABIC_INDIC)}
_DIGIT_MAP.update({ord(c): str(i) for i, c in enumerate(EASTERN_INDIC)})
_TO_ARABIC_INDIC = {ord(str(i)): c for i, c in enumerate(ARABIC_INDIC)}

TATWEEL = "ـ"
_DIACRITICS = re.compile(r"[ً-ْٰ]")
_ALEF = re.compile(r"[إأآٱ]")
_ARABIC_LETTER = re.compile(r"[ء-ي]")

ORDINALS = {
    "الأول": 1, "الاول": 1, "الثاني": 2, "الثالث": 3, "الرابع": 4, "الخامس": 5, "السادس": 6,
    "السابع": 7, "الثامن": 8, "التاسع": 9, "العاشر": 10, "الحادي عشر": 11, "الثاني عشر": 12,
    "الثالث عشر": 13, "الرابع عشر": 14, "الخامس عشر": 15, "السادس عشر": 16, "السابع عشر": 17,
    "الثامن عشر": 18, "التاسع عشر": 19, "العشرون": 20,
}
ADVERB_ORDINALS = {
    "أولاً": 1, "أولا": 1, "ثانياً": 2, "ثانيا": 2, "ثالثاً": 3, "ثالثا": 3, "رابعاً": 4, "رابعا": 4,
    "خامساً": 5, "خامسا": 5, "سادساً": 6, "سادسا": 6, "سابعاً": 7, "سابعا": 7, "ثامناً": 8, "ثامنا": 8,
    "تاسعاً": 9, "تاسعا": 9, "عاشراً": 10, "عاشرا": 10,
}

HIJRI_MONTHS = {
    "محرم": 1, "صفر": 2, "ربيع الأول": 3, "ربيع الاول": 3, "ربيع الآخر": 4, "ربيع الثاني": 4,
    "جمادى الأولى": 5, "جمادى الاولى": 5, "جمادى الآخرة": 6, "جمادى الثانية": 6, "رجب": 7,
    "شعبان": 8, "رمضان": 9, "شوال": 10, "ذو القعدة": 11, "ذي القعدة": 11, "ذو الحجة": 12,
    "ذي الحجة": 12,
}


def to_western_digits(text: str) -> str:
    return text.translate(_DIGIT_MAP)


def to_arabic_digits(text: str) -> str:
    return str(text).translate(_TO_ARABIC_INDIC)


def normalize(text: str) -> str:
    """Matching form: strip tatweel and diacritics, unify alef and ya/ta marbuta, Western digits."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = t.replace(TATWEEL, "")
    t = _DIACRITICS.sub("", t)
    t = _ALEF.sub("ا", t)
    t = t.replace("ى", "ي").replace("ة", "ه")
    t = to_western_digits(t)
    return re.sub(r"\s+", " ", t).strip()


def arabic_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if _ARABIC_LETTER.match(c)) / len(letters)


def _looks_reversed(line: str) -> bool:
    words = re.findall(r"[ء-ي]+", line)
    if len(words) < 2:
        return line.strip().startswith((".", "،", ":"))
    forward = sum(1 for w in words if w.startswith("ال") and len(w) > 3)
    backward = sum(1 for w in words if w.endswith("لا") and len(w) > 3)
    return backward > forward


def clean_text_layer(raw: str) -> str:
    """PDF text layers often hold presentation forms and visual-order lines. Repair for use as a hint."""
    t = unicodedata.normalize("NFKC", raw)
    out = []
    for line in t.splitlines():
        if _looks_reversed(line):
            line = line[::-1]
        out.append(line)
    return "\n".join(out)


def has_real_arabic_text(raw: str, min_letters: int = 40) -> bool:
    cleaned = clean_text_layer(raw)
    letters = len(_ARABIC_LETTER.findall(cleaned))
    return letters >= min_letters and arabic_ratio(cleaned) > 0.6


# ---------- clause numbering ----------

_NUM_PATTERNS = [
    re.compile(r"^\s*(?:البند|المادة|الفقرة)\s+(?:رقم\s*)?[\(\[]?\s*([0-9٠-٩]+)\s*[\)\]]?"),
    re.compile(r"^\s*(?:البند|المادة|الفقرة)\s+(" + "|".join(sorted(ORDINALS, key=len, reverse=True)) + r")"),
    re.compile(r"^\s*(" + "|".join(sorted(ADVERB_ORDINALS, key=len, reverse=True)) + r")\s*[:：\-]"),
    re.compile(r"^\s*([0-9٠-٩]+(?:\s*[-.]\s*[0-9٠-٩]+)*)\s*[-.)]\s+"),
]


def parse_clause_number(text: str) -> tuple[str | None, int | None]:
    """Recognize 'البند الأول', 'المادة (١)', 'أولاً:', '١-'. Returns (raw match, integer if known)."""
    if not text:
        return None, None
    for pat in _NUM_PATTERNS:
        m = pat.match(text)
        if not m:
            continue
        token = m.group(1)
        raw = m.group(0).strip().rstrip(":：-").strip()
        if token in ORDINALS:
            return raw, ORDINALS[token]
        if token in ADVERB_ORDINALS:
            return raw, ADVERB_ORDINALS[token]
        first = re.split(r"[-.\s]", to_western_digits(token))[0]
        return raw, int(first) if first.isdigit() else None
    return None, None


# ---------- dates ----------

_GREG = re.compile(r"([0-9٠-٩]{1,4})\s*[/\-]\s*([0-9٠-٩]{1,2})\s*[/\-]\s*([0-9٠-٩]{1,4})\s*(م|هـ|ه)?")
_HIJRI_WORDS = re.compile(
    r"([0-9٠-٩]{1,2})\s+(" + "|".join(sorted(HIJRI_MONTHS, key=len, reverse=True)) + r")\s+(?:عام|سنة)?\s*([0-9٠-٩]{4})"
)


def parse_date(raw: str, calendar_hint: str = "unknown") -> tuple[str, str | None]:
    """Return (calendar, ISO Gregorian date or None). Hijri dates convert via hijri-converter."""
    from hijri_converter import Hijri

    text = raw.strip()
    m = _HIJRI_WORDS.search(text)
    if m:
        d, month, y = int(to_western_digits(m.group(1))), HIJRI_MONTHS[m.group(2)], int(to_western_digits(m.group(3)))
        try:
            return "hijri", Hijri(y, month, d).to_gregorian().isoformat()
        except (ValueError, OverflowError):
            return "hijri", None
    m = _GREG.search(text)
    if not m:
        return calendar_hint, None
    a, b, c = (int(to_western_digits(g)) for g in m.group(1, 2, 3))
    year, month, day = (a, b, c) if a > 31 else (c, b, a)
    suffix = m.group(4)
    calendar = "hijri" if suffix in ("هـ", "ه") or (calendar_hint == "hijri") or 1300 <= year <= 1500 else "gregorian"
    try:
        if calendar == "hijri":
            return calendar, Hijri(year, month, day).to_gregorian().isoformat()
        return calendar, date(year, month, day).isoformat()
    except (ValueError, OverflowError):
        return calendar, None


def hijri_today_ar(today: date | None = None) -> str:
    from hijri_converter import Gregorian

    today = today or date.today()
    h = Gregorian(today.year, today.month, today.day).to_hijri()
    month_names = ["محرم", "صفر", "ربيع الأول", "ربيع الآخر", "جمادى الأولى", "جمادى الآخرة", "رجب",
                   "شعبان", "رمضان", "شوال", "ذو القعدة", "ذو الحجة"]
    return f"{to_arabic_digits(h.day)} {month_names[h.month - 1]} {to_arabic_digits(h.year)}هـ"


def gregorian_today_ar(today: date | None = None) -> str:
    today = today or date.today()
    return to_arabic_digits(today.strftime("%d/%m/%Y")) + "م"


def find_dates(text: str) -> list[tuple[str, str]]:
    """(raw, calendar) for every Hijri or Gregorian date written in the text."""
    found = []
    for m in _HIJRI_WORDS.finditer(text):
        found.append((m.group(0).strip(), "hijri"))
    for m in _GREG.finditer(text):
        raw = m.group(0).strip()
        calendar, _ = parse_date(raw)
        found.append((raw, calendar))
    return found


# ---------- counted nouns (العدد والمعدود) ----------

# (one, two, plural for 3-10, accusative singular for 11-99, genitive singular for 100s)
COUNTED = {
    "page": ("صفحة واحدة", "صفحتان", "صفحات", "صفحة", "صفحة"),
    "enhanced_page": ("صفحة واحدة محسّنة", "صفحتان محسّنتان", "صفحات محسّنة", "صفحة محسّنة", "صفحة محسّنة"),
    "line": ("سطر واحد", "سطران", "أسطر", "سطراً", "سطر"),
    "clause": ("بند واحد", "بندان", "بنود", "بنداً", "بند"),
    "stamp": ("ختم واحد", "ختمان", "أختام", "ختماً", "ختم"),
    "segment": ("مقطع واحد", "مقطعان", "مقاطع", "مقطعاً", "مقطع"),
    "field": ("حقل واحد", "حقلان", "حقول", "حقلاً", "حقل"),
    "filled_field": ("حقل واحد معبّأ", "حقلان معبّآن", "حقول معبّأة", "حقلاً معبّأً", "حقل معبّأ"),
    "low_field": ("حقل واحد منخفض الدقة", "حقلان منخفضا الدقة", "حقول منخفضة الدقة", "حقلاً منخفض الدقة",
                  "حقل منخفض الدقة"),
    "highlighted_field": ("حقل واحد مظلل", "حقلان مظللان", "حقول مظللة", "حقلاً مظللاً", "حقل مظلل"),
    "redline": ("تعديل مقترح واحد", "تعديلان مقترحان", "تعديلات مقترحة", "تعديلاً مقترحاً", "تعديل مقترح"),
    "party": ("طرف واحد", "طرفان", "أطراف", "طرفاً", "طرف"),
    "date": ("تاريخ واحد", "تاريخان", "تواريخ", "تاريخاً", "تاريخ"),
    "token": ("رمز واحد", "رمزان", "رموز", "رمزاً", "رمز"),
}


def count_ar(n: int, noun: str) -> str:
    """Number with its noun in correct Arabic agreement: صفحة واحدة، صفحتان، ٥ صفحات، ١٢ صفحةً، ١٠٠ صفحة."""
    one, two, plural, acc, gen = COUNTED[noun]
    if n == 1:
        return one
    if n == 2:
        return two
    rest = n % 100
    if n == 0 or 3 <= rest <= 10:
        word = plural
    elif rest == 0 or rest in (1, 2):
        word = gen
    else:
        word = acc
    return f"{to_arabic_digits(n)} {word}"
