"""Turkish legal rule-based item extractor for MESA.

Standard library only. Layer 0 (mesa_storage).
Schema: mesa_bench/extract/schema.json v1.3.
Extracts RULE types: citation, decision_ref, date, amount, duration, outcome.
"""

import re
from collections.abc import Iterator
from typing import Any, Dict, List, Optional, Tuple, cast

RULES_VERSION = "tr-legal-rules-3"

def _up_to(piece: str, count: int) -> str:
    """Spell out a small bounded sequence without nested regex repetitions.

    Each piece has bounded terminal runs separated by delimiters. Sharing the
    prefix avoids rescanning it for every possible list length. Empty branches
    let the following clause select the end, without a repeated regex group.
    """
    tail = ""
    for _ in range(count):
        tail = "(?:" + piece + tail + "|)"
    return tail


def _span_ends(length: int, spans: list[tuple[int, int]]) -> list[int]:
    """Maximum end of any span starting at or before each character, O(n)."""
    ends = [0] * (length + 1)
    if not spans:
        return ends
    for start, end in spans:
        ends[start] = max(ends[start], end)
    furthest = 0
    for i, end in enumerate(ends):
        if end > furthest:
            furthest = end
        ends[i] = furthest
    return ends


_ARTICLE_NUMBER = r"\d{1,12}+" + _up_to(r"\.\d{1,12}+", 5)

# ----------------------------------------------------------------------
# 1. Turkish number parsing
# ----------------------------------------------------------------------
_NUM_TOKENS: List[Tuple[str, int]] = [
    ("milyar", 1000000000),
    ("milyon", 1000000),
    ("bin", 1000),
    ("yüz", 100),
    ("yuz", 100),
    ("doksan", 90),
    ("seksen", 80),
    ("yetmiş", 70),
    ("yetmis", 70),
    ("altmış", 60),
    ("altmis", 60),
    ("elli", 50),
    ("kırk", 40),
    ("kirk", 40),
    ("otuz", 30),
    ("yirmi", 20),
    ("on", 10),
    ("dokuz", 9),
    ("sekiz", 8),
    ("yedi", 7),
    ("altı", 6),
    ("alti", 6),
    ("beş", 5),
    ("bes", 5),
    ("dört", 4),
    ("dort", 4),
    ("üç", 3),
    ("uc", 3),
    ("iki", 2),
    ("bir", 1),
]


def parse_turkish_number(s: str) -> Optional[int]:
    """Parse integer or Turkish number words into int."""
    s = s.strip()
    if s.isdigit():
        return int(s)
    words = s.lower().replace("’", "'").split()
    flat_tokens: List[Tuple[str, int]] = []
    for w in words:
        w_clean = re.sub(r"[^a-zçğıöşü]", "", w)
        if not w_clean:
            continue
        i = 0
        while i < len(w_clean):
            matched = False
            for tok, val in _NUM_TOKENS:
                if w_clean.startswith(tok, i):
                    flat_tokens.append((tok, val))
                    i += len(tok)
                    matched = True
                    break
            if not matched:
                return None
    if not flat_tokens:
        return None

    total = 0
    current_scale_acc = 0
    current_hundreds_acc = 0

    for tok, val in flat_tokens:
        if val in (1000000000, 1000000, 1000):
            scale_multiplier = val
            num = current_scale_acc + current_hundreds_acc
            if num == 0:
                num = 1
            total += num * scale_multiplier
            current_scale_acc = 0
            current_hundreds_acc = 0
        elif val == 100:
            h_mult = current_hundreds_acc if current_hundreds_acc > 0 else 1
            current_scale_acc += h_mult * 100
            current_hundreds_acc = 0
        else:
            current_hundreds_acc += val

    total += current_scale_acc + current_hundreds_acc
    return total


# ----------------------------------------------------------------------
# 2. Durations
# ----------------------------------------------------------------------
_NUM_ROOTS_STR = (
    r"(?:bir|iki|üç|uc|dört|dort|beş|bes|altı|alti|yedi|sekiz|dokuz|"
    r"on|yirmi|otuz|kırk|kirk|elli|altmış|altmis|yetmiş|yetmis|seksen|doksan|"
    r"yüz|yuz|bin|milyon|milyar)"
)
_NUM_WORD_TOKEN_STR = r"(?:" + _NUM_ROOTS_STR + r"[a-zçğıöşü]{0,64}+)"
_NUMBER_TOKEN = re.compile(r"\b(?:\d{1,12}+|" + _NUM_WORD_TOKEN_STR + r"\b)", re.IGNORECASE)


def _number_spans(text: str, *, words_only: bool = False) -> Iterator[tuple[int, int]]:
    """Collect at most twelve adjacent number words in a single token pass."""
    chain: List[int] = []
    previous_end = -1
    for token in _NUMBER_TOKEN.finditer(text):
        start, end = token.span()
        word = not token.group().isdigit()
        gap = text[previous_end:start] if previous_end >= 0 else ""
        if not word or not chain or not (0 < len(gap) <= 4 and gap.isspace()):
            chain = []
        if word:
            chain.append(start)
            chain = chain[-12:]
        if word or not words_only:
            yield (chain[0] if word else start), end
        previous_end = end


_DURATION_PAT = re.compile(
    r"(?:(?<=\d)\s{0,4}+|(?<=[a-zçğıöşü])\s{1,4}+)"
    r"(?P<unit_full>"
    r"(?P<unit_lemma>i\s{0,4}+ş\s{1,4}+g\s{0,4}+ü\s{0,4}+n\s{0,4}+ü|g\s{0,4}+ü\s{0,4}+n|h\s{0,4}+a\s{0,4}+f\s{0,4}+t\s{0,4}+a|a\s{0,4}+y|y\s{0,4}+ı\s{0,4}+l|s\s{0,4}+a\s{0,4}+a\s{0,4}+t|(?:eğitim-öğretim\s{1,4}+)?y\s{0,4}+a\s{0,4}+r\s{0,4}+ı\s{0,4}+y\s{0,4}+ı\s{0,4}+l)"
    r"[a-zçğıöşü]{0,64}+"
    r")\b",
    re.IGNORECASE,
)

_MONTH_NAMES = {
    "ocak", "şubat", "mart", "nisan", "mayıs", "haziran", "temmuz",
    "ağustos", "eylül", "ekim", "kasım", "aralık"
}


def extract_durations(text: str) -> List[Dict[str, Any]]:
    items = []
    for start_pos, num_end in _number_spans(text):
        m = _DURATION_PAT.match(text, num_end)
        if m is None:
            continue
        # Suppress match if preceded by date like 14.6.1982 gün
        prefix = text[max(0, start_pos - 10):start_pos]
        if re.search(r"\d{1,12}+[./]\s{0,4}+$", prefix):
            continue

        num_str = text[start_pos:num_end].strip()
        words = num_str.split()
        parsed_val = None
        best_words: List[str] = []
        for i in range(len(words), 0, -1):
            cand = " ".join(words[:i])
            val_num = parse_turkish_number(cand)
            if val_num is not None and val_num > 0:
                parsed_val = val_num
                best_words = words[:i]
                break
        if parsed_val is None:
            continue

        unit_raw = re.sub(r"\s{1,4}+", "", m.group("unit_lemma").lower())
        if "yarıyıl" in unit_raw or "yariyil" in unit_raw:
            unit = "yarıyıl"
        elif "işgünü" in unit_raw or "isgunu" in unit_raw:
            unit = "iş günü"
        elif "gün" in unit_raw or "gun" in unit_raw:
            unit = "gün"
        elif "hafta" in unit_raw:
            unit = "hafta"
        elif "ay" in unit_raw:
            p = text[max(0, start_pos - 15):start_pos].lower().strip()
            if any(p.endswith(mon) for mon in _MONTH_NAMES):
                continue
            unit = "ay"
        elif "yıl" in unit_raw or "yil" in unit_raw:
            unit = "yıl"
        elif "saat" in unit_raw:
            unit = "saat"
        else:
            continue

        unit_lemma_end = m.start("unit_lemma") + len(m.group("unit_lemma"))
        cand_str = " ".join(best_words)
        sub_num = text[start_pos:m.start("unit_full")]
        cand_offset = sub_num.find(cand_str)
        span_start = start_pos + (cand_offset if cand_offset != -1 else 0)
        span_end = unit_lemma_end
        span_text = text[span_start:span_end]

        items.append({
            "start": span_start,
            "end": span_end,
            "text": span_text,
            "type": "duration",
            "value": parsed_val,
            "unit": unit,
        })
    return items


# ----------------------------------------------------------------------
# 3. Amounts
# ----------------------------------------------------------------------
_DIGIT_NUM_STR = r"(?:\d{1,3}\.\d{3}" + _up_to(r"\.\d{3}", 5) + r"(?:,\d{1,2})?|\d{1,12}+(?:,\d{1,2})?)"

_CURRENCY_PAT_STR = (
    r"(?:"
    r"TL|T\.L\.|₺|"
    r"(?:Türk\s{1,4}+)?[Ll]\s{0,4}+i\s{0,4}+r\s{0,4}+a\s{0,4}+s\s{0,4}+ı[a-zçğıöşü]{0,64}+|"
    r"(?:Türk\s{1,4}+)?[Ll]\s{0,4}+i\s{0,4}+r\s{0,4}+a[a-zçğıöşü]{0,64}+|"
    r"avro|euro|eur|€|"
    r"dolar[a-zçğıöşü]{0,64}+|ABD\s{0,4}+Doları[a-zçğıöşü]{0,64}+|usd|\$"
    r")\b"
)

_DIGIT_AMOUNT_PAT = re.compile(
    r"\b(?P<num>" + _DIGIT_NUM_STR + r")(?:\s{0,4}+-\s{0,4}+|\s{0,4}+)(?P<curr>" + _CURRENCY_PAT_STR + r")",
    re.IGNORECASE,
)

_WORD_AMOUNT_PAT = re.compile(r"\s{1,4}+(?P<curr>" + _CURRENCY_PAT_STR + r")", re.IGNORECASE)


def extract_amounts(text: str) -> List[Dict[str, Any]]:
    items = []
    seen = set()

    # 1. Digits
    for m in _DIGIT_AMOUNT_PAT.finditer(text):
        num_str = m.group("num").strip()
        curr_str = m.group("curr").strip().lower()

        if "," in num_str:
            parts = num_str.split(",")
            val = f"{parts[0].replace('.', '')}.{parts[1]}"
        else:
            val = num_str.replace(".", "")

        if any(c in curr_str for c in ["tl", "türk", "t.l.", "₺", "lira"]):
            currency = "TRY"
        elif any(c in curr_str for c in ["dolar", "usd", "$"]):
            currency = "USD"
        elif any(c in curr_str for c in ["avro", "euro", "eur", "€"]):
            currency = "EUR"
        else:
            currency = "TRY"

        items.append({
            "start": m.start(),
            "end": m.end(),
            "text": text[m.start():m.end()],
            "type": "amount",
            "value": val,
            "currency": currency,
        })
        seen.add((m.start(), m.end()))

    # 2. Written-out words
    digit_spans = _span_ends(len(text), list(seen))
    for num_start, num_end in _number_spans(text, words_only=True):
        word_match = _WORD_AMOUNT_PAT.match(text, num_end)
        if word_match is None:
            continue
        m = word_match
        num_str = text[num_start:num_end].strip()
        curr_str = m.group("curr").strip().lower()

        words = num_str.split()
        parsed_val = None
        best_words: List[str] = []
        for i in range(len(words), 0, -1):
            cand = " ".join(words[:i])
            val_num = parse_turkish_number(cand)
            if val_num is not None:
                parsed_val = val_num
                best_words = words[:i]
                break
        if parsed_val is None:
            continue

        sub_text = text[num_start:m.end()]
        curr_match = re.search(_CURRENCY_PAT_STR, sub_text, re.IGNORECASE)
        if not curr_match:
            continue
        end = num_start + curr_match.end()
        cand_idx = sub_text.find(best_words[0])
        if cand_idx == -1:
            continue
        start = num_start + cand_idx

        # Strip any leading quote characters
        span_str = text[start:end]
        lstrip_len = len(span_str) - len(span_str.lstrip("“\"'‘` "))
        start += lstrip_len
        span_str = text[start:end]

        if digit_spans[end - 1] > start:
            continue

        if any(c in curr_str for c in ["tl", "türk", "t.l.", "₺", "lira"]):
            currency = "TRY"
        elif any(c in curr_str for c in ["dolar", "usd", "$"]):
            currency = "USD"
        elif any(c in curr_str for c in ["avro", "euro", "eur", "€"]):
            currency = "EUR"
        else:
            currency = "TRY"

        items.append({
            "start": start,
            "end": end,
            "text": span_str,
            "type": "amount",
            "value": str(parsed_val),
            "currency": currency,
        })
        seen.add((start, end))

    items.sort(key=lambda x: cast(int, x["start"]))
    return items


# ----------------------------------------------------------------------
# 4. Dates
# ----------------------------------------------------------------------
_MONTHS = {
    "ocak": "01", "şubat": "02", "subat": "02", "mart": "03", "nisan": "04",
    "mayıs": "05", "mayis": "05", "haziran": "06", "temmuz": "07",
    "ağustos": "08", "agustos": "08", "eylül": "09", "eylul": "09",
    "ekim": "10", "kasım": "11", "kasim": "11", "aralık": "12", "aralik": "12",
}

_NUMERIC_DATE_PAT = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b")
_TEXT_DATE_PAT = re.compile(
    r"\b(\d{1,2})\s{1,4}+(Ocak|Şubat|Subat|Mart|Nisan|Mayıs|Mayis|Haziran|Temmuz|Ağustos|Agustos|Eylül|Eylul|Ekim|Kasım|Kasim|Aralık|Aralik)\s{1,4}+(\d{4})\b",
    re.IGNORECASE,
)
_PARTIAL_DATE_PAT = re.compile(
    r"\b(Ocak|Şubat|Subat|Mart|Nisan|Mayıs|Mayis|Haziran|Temmuz|Ağustos|Agustos|Eylül|Eylul|Ekim|Kasım|Kasim|Aralık|Aralik)\s{1,4}+(\d{4})\b",
    re.IGNORECASE,
)


def extract_dates(text: str) -> List[Dict[str, Any]]:
    items = []
    # 1. Numeric dates
    for m in _NUMERIC_DATE_PAT.finditer(text):
        d_str, m_str, y_str = m.groups()
        day, month, year = int(d_str), int(m_str), int(y_str)
        if 1 <= day <= 31 and 1 <= month <= 12 and 1900 <= year <= 2099:
            items.append({
                "start": m.start(),
                "end": m.end(),
                "text": text[m.start():m.end()],
                "type": "date",
                "value": f"{year:04d}-{month:02d}-{day:02d}",
            })

    # 2. Text dates
    for m in _TEXT_DATE_PAT.finditer(text):
        d_str, m_name, y_str = m.groups()
        day = int(d_str)
        month_code = _MONTHS.get(m_name.lower())
        year = int(y_str)
        if month_code and 1 <= day <= 31 and 1900 <= year <= 2099:
            items.append({
                "start": m.start(),
                "end": m.end(),
                "text": text[m.start():m.end()],
                "type": "date",
                "value": f"{year:04d}-{month_code}-{day:02d}",
            })

    # 3. Partial dates
    text_date_spans: List[Tuple[int, int]] = [
        (cast(int, it["start"]), cast(int, it["end"])) for it in items
    ]
    date_ends = _span_ends(len(text), text_date_spans)
    for m in _PARTIAL_DATE_PAT.finditer(text):
        m_start, m_end = m.start(), m.end()
        if m_end <= date_ends[m_start]:
            continue
        m_name, y_str = m.groups()
        month_code = _MONTHS.get(m_name.lower())
        year = int(y_str)
        if month_code and 1900 <= year <= 2099:
            items.append({
                "start": m_start,
                "end": m_end,
                "text": text[m_start:m_end],
                "type": "date",
                "value": f"{year:04d}-{month_code}",
            })

    return items


# ----------------------------------------------------------------------
# 5. Decision Refs
# ----------------------------------------------------------------------
_DOCKET_PAT_STR = r"(?:\d{4}/(?:\(\d{1,12}+\))?[\d-]{1,64}+|\d{4}/\d{1,12}+)"
_DECISION_PAT_STR = r"\d{4}/\d{1,12}+"
_DATE_PAT_STR = r"\d{1,2}[./]\d{1,2}[./]\d{4}"
_DATE_CLAUSE_STR = r"(?P<date_pre>" + _DATE_PAT_STR + r")\s{0,4}+(?:t\s{0,4}+a\s{0,4}+r\s{0,4}+i?\s{0,4}+h(?:l\s{0,4}+i?)?|g\s{0,4}+ü\s{0,4}+n)(?:[,\s]{1,4}+(?:ve\s{0,4}+)?)?"

_LOC_PREFIX_STR = r"(?-i:[A-ZÇĞİÖŞÜ][a-zçğıöşü]{1,64}+(?:\s{1,4}+[A-ZÇĞİÖŞÜ][a-zçğıöşü]{1,64}+)?\s{1,4}+)"

_COURT_CHAM_PAT_STR = (
    r"(?P<court_cham>"
    r"(?:(?:Yargıtay|Danıştay|Y\.C\.G\.K)\s{1,4}+)?"
    r"(?:\((?:kapanan|kapatılan)\)\s{1,4}+)?(?:\d{1,12}+\.\s{1,4}+)?(?:Ceza|Hukuk)?\s{0,4}+(?:Genel\s{1,4}+Kurulu?[\w]{0,64}+|Dairesi[\w]{0,64}+)|"
    r"(?:(?:" + _LOC_PREFIX_STR + r")?\d{0,12}+\.?\s{0,4}+)?(?:Asliye\s{1,4}+(?:Hukuk|Ceza)|İş|Ticaret|Sulh\s{1,4}+(?:Hukuk|Ceza)|Bölge\s{1,4}+Adliye|Ağır\s{1,4}+Ceza)\s{1,4}+Mahkemes[a-zçğıöşü]{0,64}+|"
    r"Özel\s{1,4}+Dairece|Dairece|Mahkemece|Anayasa\s{1,4}+M\s{0,4}+a\s{0,4}+h\s{0,4}+k?\s{0,4}+e\s{0,4}+m\s{0,4}+e\s{0,4}+s\s{0,4}+i[’'\w]{0,64}+|Y\.C\.G\.K"
    r")\s{0,4}+(?:verilen\s{1,4}+)?"
)

# Pattern 1: Docket E., Decision K.
_DEC_REF_PAT1 = re.compile(
    r"(?:" + _COURT_CHAM_PAT_STR + r")?"
    r"(?:" + _DATE_CLAUSE_STR + r")?"
    r"(?P<docket>" + _DOCKET_PAT_STR + r")\s{0,4}+(?:E\.|Esas)"
    r"[,\s]{1,4}+"
    r"(?P<decision>" + _DECISION_PAT_STR + r")\s{0,4}+(?:K\.|Karar)"
    r"(?:[,\s]{1,4}+(?P<date_post>" + _DATE_PAT_STR + r")\s{0,4}+(?:t\s{0,4}+a\s{0,4}+r\s{0,4}+i?\s{0,4}+h(?:l\s{0,4}+i?)?|g\s{0,4}+ü\s{0,4}+n))?",
    re.IGNORECASE,
)

# Pattern 2: Anayasa Mahkemesi style: E. <docket>, K. <decision>
_DEC_REF_PAT2 = re.compile(
    r"(?:" + _COURT_CHAM_PAT_STR + r")?"
    r"(?:" + _DATE_CLAUSE_STR + r")?"
    r"(?:E\.|E\.:|Esas\s{1,4}+No:?)\s{0,4}+(?P<docket>" + _DOCKET_PAT_STR + r")"
    r"[,\s]{1,4}+"
    r"(?:K\.|K\.:|Karar\s{1,4}+No:?)\s{0,4}+(?P<decision>" + _DECISION_PAT_STR + r")",
    re.IGNORECASE,
)

# Pattern 3: Single K pattern (e.g. 'Y.C.G.K 2020/527 K')
_DEC_REF_PAT3 = re.compile(
    r"(?P<court_cham>Y\.C\.G\.K)\s{1,4}+(?P<decision>\d{4}/\d{1,12}+)\s{0,4}+K\b",
    re.IGNORECASE,
)

# Pattern 4: Short form <court/chamber>? <date> tarih(li) ve <n>-<m> (sayılı|sayı)?
_DEC_REF_PAT4 = re.compile(
    r"(?:(?P<court_cham>"
    r"(?:(?:(?:" + _LOC_PREFIX_STR + r")?Bölge\s{1,4}+Adliye\s{1,4}+Mahkemesi|Yargıtay|Danıştay|Y\.C\.G\.K)\s{1,4}+)?\s{0,4}+"
    r"(?:\((?:kapanan|kapatılan)\)\s{1,4}+)?(?:\d{1,12}+\.\s{1,4}+)?(?:Ceza|Hukuk)?\s{0,4}+(?:Genel\s{1,4}+Kurulu?[\w]{0,64}+|Dairesi[\w]{0,64}+)|"
    r"(?:(?:" + _LOC_PREFIX_STR + r")?\d{0,12}+\.?\s{0,4}+)?(?:Asliye\s{1,4}+(?:Hukuk|Ceza)|İş|Ticaret|Sulh\s{1,4}+(?:Hukuk|Ceza)|Bölge\s{1,4}+Adliye|Ağır\s{1,4}+Ceza)\s{1,4}+Mahkemes[a-zçğıöşü]{0,64}+|"
    r"Özel\s{1,4}+Dairece|Dairece|Mahkemece|Anayasa\s{1,4}+M\s{0,4}+a\s{0,4}+h\s{0,4}+k?\s{0,4}+e\s{0,4}+m\s{0,4}+e\s{0,4}+s\s{0,4}+i[’'\w]{0,64}+|Y\.C\.G\.K"
    r")\s{0,4}+(?:verilen\s{1,4}+)?)?"
    r"(?P<date>" + _DATE_PAT_STR + r")\s{0,4}+(?:t\s{0,4}+a\s{0,4}+r\s{0,4}+i?\s{0,4}+h(?:l\s{0,4}+i?)?|g\s{0,4}+ü\s{0,4}+n)[,\s]{1,4}+(?:ve\s{0,4}+)?"
    r"(?P<docket>\d{1,12}+)\s{0,4}+-\s{0,4}+(?P<decision>\d{1,12}+)(?:\s{0,4}+(?:sayılı|sayı))?",
    re.IGNORECASE,
)

# Pattern 5: Single Esas with court (e.g. 'Bandırma Sulh Hukuk Mahkemesinde görülen 2009/183 Esas')
_DEC_REF_PAT5 = re.compile(
    r"(?P<court_cham>"
    r"(?:(?:" + _LOC_PREFIX_STR + r")?\d{0,12}+\.?\s{0,4}+)?(?:Asliye\s{1,4}+(?:Hukuk|Ceza)|İş|Ticaret|Sulh\s{1,4}+(?:Hukuk|Ceza)|Bölge\s{1,4}+Adliye|Ağır\s{1,4}+Ceza)\s{1,4}+Mahkemes[a-zçğıöşü]{0,64}+|"
    r"(?:Yargıtay|Danıştay)\s{1,4}+(?:\d{1,12}+\.\s{1,4}+)?(?:Ceza|Hukuk)\s{1,4}+Dairesi[\w]{0,64}+"
    r")\s{0,4}+(?:verilen\s{1,4}+|kayıtlı\s{1,4}+|görülen\s{1,4}+)(?P<docket>\d{4}/\d{1,12}+)\s{0,4}+(?:E\.|Esas)\b(?!\s{0,4}+,\s{0,4}+\d{4}/\d{1,12}+\s{0,4}+(?:K\.|Karar))",
    re.IGNORECASE,
)


def _clean_court_chamber(raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Split and clean court and chamber names from matched court_cham text."""
    if not raw:
        return None, None
    raw_str = re.sub(r"\s{1,4}+", " ", raw.strip())
    raw_str = re.sub(r"\((?:kapatılan|kapanan)\)\s{0,4}+", "", raw_str, flags=re.IGNORECASE).strip()
    raw_str = re.sub(r"[’'\x27\u2032][a-zçğıöşü]{1,64}+$", "", raw_str, flags=re.IGNORECASE).strip()
    raw_str = re.sub(r"Mahkemes(?:i[’'\x27\u2032]?(?:nce|nin|ne|nde)|ince|inin)$", "Mahkemesi", raw_str, flags=re.IGNORECASE)
    raw_str = re.sub(r"Daires(?:i[’'\x27\u2032]?(?:nce|nin|ne|nde)|ince|inin)$", "Dairesi", raw_str, flags=re.IGNORECASE)
    raw_str = re.sub(r"Kurul(?:u[’'\x27\u2032]?(?:nca|nun|na|nda)|unca|unun)$", "Kurulu", raw_str, flags=re.IGNORECASE)
    raw_str = re.sub(r"Dairece$", "Dairesi", raw_str, flags=re.IGNORECASE)
    raw_str = re.sub(r"Mahkemece$", "Mahkemesi", raw_str, flags=re.IGNORECASE)
    raw_str = raw_str.strip()

    if "anayasa mahkemesi" in raw_str.lower():
        return "Anayasa Mahkemesi", None
    elif "danıştay" in raw_str.lower() or "danistay" in raw_str.lower():
        court = "Danıştay"
        cham = re.sub(r"danıştay|danistay", "", raw_str, flags=re.IGNORECASE).strip()
        return court, (cham if cham else None)
    elif "bölge adliye mahkemesi" in raw_str.lower():
        m = re.match(r"^(.{0,256}?Bölge\s{1,4}+Adliye\s{1,4}+Mahkemesi)\s{0,4}+(.{0,256})$", raw_str, re.IGNORECASE)
        if m:
            court = m.group(1).strip()
            cham = m.group(2).strip()
            return court, (cham if cham else None)
        return raw_str, None
    elif "yargıtay" in raw_str.lower() or "y.c.g.k" in raw_str.lower() or raw_str.lower().startswith("ceza genel") or raw_str.lower().startswith("hukuk genel"):
        court = "Yargıtay"
        chamber: Optional[str] = None
        if raw_str.upper() == "Y.C.G.K":
            chamber = "Ceza Genel Kurulu"
        else:
            cham = re.sub(r"yargıtay", "", raw_str, flags=re.IGNORECASE).strip()
            chamber = cham if cham else None
        return court, chamber
    elif "mahkeme" in raw_str.lower():
        return None, raw_str
    else:
        return raw_str, None


_DECISION_HINT = re.compile(r"E\.|K\b|Esas|Karar", re.IGNORECASE)


def extract_decision_refs(text: str) -> List[Dict[str, Any]]:
    if "/" not in text and "-" not in text:
        return []
    if not _DECISION_HINT.search(text) and not _NUMERIC_DATE_PAT.search(text):
        return []
    items = []
    seen_spans: set[Tuple[int, int]] = set()

    for pat in [_DEC_REF_PAT2, _DEC_REF_PAT1, _DEC_REF_PAT4, _DEC_REF_PAT5, _DEC_REF_PAT3]:
        previous_ends = _span_ends(len(text), list(seen_spans))
        for m in pat.finditer(text):
            start, end = m.start(), m.end()
            span_str = text[start:end]

            # Strip leading whitespace
            lstrip_len = len(span_str) - len(span_str.lstrip())
            start += lstrip_len
            span_str = text[start:end]

            # Strip leading unrelated words ('verilen', 'ilişkin', 'sayılı')
            for bad_prefix in ["verilen ", "ilişkin ", "sayılı "]:
                if span_str.lower().startswith(bad_prefix):
                    start += len(bad_prefix)
                    span_str = text[start:end]
                    lstrip_len = len(span_str) - len(span_str.lstrip())
                    start += lstrip_len
                    span_str = text[start:end]

            rstrip_len = len(span_str) - len(span_str.rstrip(" ,."))
            end -= rstrip_len
            span_str = text[start:end]

            if not span_str:
                continue

            span = (start, end)
            if end <= previous_ends[start]:
                continue

            groups = m.groupdict()
            court_cham_raw = groups.get("court_cham")
            docket = groups.get("docket")
            decision = groups.get("decision")
            date_raw = groups.get("date_pre") or groups.get("date_post") or groups.get("date")

            court, chamber = _clean_court_chamber(court_cham_raw)

            date_val = None
            if date_raw:
                d_m = _NUMERIC_DATE_PAT.search(date_raw)
                if d_m:
                    day, month, year = int(d_m.group(1)), int(d_m.group(2)), int(d_m.group(3))
                    date_val = f"{year:04d}-{month:02d}-{day:02d}"

            items.append({
                "start": start,
                "end": end,
                "text": span_str,
                "type": "decision_ref",
                "court": court,
                "chamber": chamber,
                "docket_no": docket,
                "decision_no": decision,
                "date": date_val,
            })
            seen_spans.add(span)

    return items


# ----------------------------------------------------------------------
# 6. Citations
# ----------------------------------------------------------------------
_ORDINAL_MAP = {
    "birinci": "1", "ikinci": "2", "üçüncü": "3", "ucuncu": "3",
    "dördüncü": "4", "dorduncu": "4", "beşinci": "5", "besinci": "5",
    "altıncı": "6", "altinci": "6", "yedinci": "7", "sekizinci": "8",
    "dokuzuncu": "9", "onuncu": "10", "onbeşinci": "15",
}

_KNOWN_ACRONYMS = {
    "TCK", "TMK", "HMK", "İİK", "IIK", "CMK", "CMUK", "İYUK", "IYUK",
    "TTK", "VUK", "DMK", "PVSK", "AİHS", "TBK"
}

_APOSTROPHE_STR = r"[’'\x27\u2032\u2018\u201b]"


def _clean_law_name(header: str) -> Optional[str]:
    if not header:
        return None
    header = re.sub(r"\s{1,4}+", " ", header.strip())
    header = re.sub(r"^Mülga\s{1,4}+", "", header)
    header = re.sub(r"\s{0,4}+\([^\)\n]{1,60}\)\s{0,4}+", " ", header).strip()
    header = re.sub(r"[’'\x27\u2032\u2018\u201b]\s{0,4}+[a-zçğıöşü]{1,64}+$", "", header).strip()

    lower = header.lower()
    if lower.startswith("aynı ") or lower.startswith("bu ") or lower == "aynı" or lower == "bu" or lower.startswith("anılan "):
        return None

    for sfx in ["nun", "nün", "nin", "nın", "unun", "ünün", "inin", "ının"]:
        if lower.endswith(sfx) and len(header) > len(sfx) + 3:
            header = header[:-len(sfx)]
            break

    if header.endswith("Kanu"):
        header += "n"
    elif header.endswith("Yönetmeliğ"):
        header += "i"
    elif header.endswith("Tebliğ"):
        header += "i"
    elif header.endswith("Kararname"):
        header += "si"
    return header.strip()


def _parse_articles_and_paragraphs(body_text: str) -> Tuple[Optional[str], Optional[str]]:
    lower = body_text.lower()
    if "geçici" in lower:
        m_range = re.search(r"geçici\s{1,4}+(\d{1,12}+)\s{1,4}+il[aâ]\s{1,4}+geçici\s{1,4}+(\d{1,12}+)", body_text, re.IGNORECASE)
        if m_range:
            start_n, end_n = int(m_range.group(1)), int(m_range.group(2))
            # Never allocate an input-controlled range of millions of articles.
            if end_n - start_n > 128:
                return f"geçici {start_n}, geçici {end_n}", None
            arts = [f"geçici {i}" for i in range(start_n, end_n + 1)]
            return ", ".join(arts), None

        m_gm = re.search(r"Geçici\s{1,4}+madde\s{1,4}+(\d{1,12}+)", body_text, re.IGNORECASE)
        if m_gm:
            return f"Geçici {m_gm.group(1)}", None

    if "il" in lower:
        m_ila = re.search(r"\b(\d{1,12}+)\s{1,4}+il[aâ]\s{1,4}+(\d{1,12}+)\s{0,4}+(?:[ıiuü]?nc[ıiuü]|\.)?\s{0,4}+madde", body_text, re.IGNORECASE)
        if m_ila:
            return f"{m_ila.group(1)}, {m_ila.group(2)}", None

    if "tbk" in lower:
        m_tbk = re.search(r"\bTBK/(\d{1,12}+)\b", body_text)
        if m_tbk:
            return m_tbk.group(1), None

    articles = []
    paras = []

    m_list = re.findall(r"\bm\.\s{0,4}+(" + _ARTICLE_NUMBER + r")(?:/(\d{1,12}+))?", body_text)
    if len(m_list) >= 2:
        for a, p in m_list:
            articles.append(a)
            paras.append(p if p else None)
    else:
        clauses = re.split(r"(?:,\s{0,4}+|\s{1,4}+(?:ve|veya|ile|ilâ|ila)\s{1,4}+)", body_text)
        for cl in clauses:
            cl = cl.strip()
            if not cl:
                continue
            art_m = re.search(
                r"\b(?:m\.\s{0,4}+)?((?:[Gg]eçici\s{1,4}+|[Ee]k\s{1,4}+|[Mm]ükerrer\s{1,4}+|[Mm]ülga\s{1,4}+)?" + _ARTICLE_NUMBER + r"(?:/[A-Za-zÇĞİÖŞÜçğıöşü])?)(?:/(\d{1,12}+))?",
                cl,
                re.IGNORECASE,
            )
            if art_m:
                a_val = re.sub(r"\s{1,4}+", " ", art_m.group(1).strip())
                if a_val.lower().startswith("mükerrer "):
                    a_val = "mükerrer " + a_val.split()[1]
                p_val = art_m.group(2)
                if not p_val:
                    pw_m = re.search(
                        r"\b(birinci|ikinci|üçüncü|dördüncü|beşinci|altıncı|yedinci|sekizinci|dokuzuncu|onuncu|onbeşinci|\d{1,12}+)\s{0,4}+(?:[ıiuü]?nc[ıiuü]|\.)?\s{0,4}+f\s{0,4}+ı\s{0,4}+k\s{0,4}+r\s{0,4}+a",
                        cl,
                        re.IGNORECASE,
                    )
                    if pw_m:
                        w = pw_m.group(1).lower()
                        p_val = _ORDINAL_MAP.get(w, w)
                if not p_val:
                    sl_m = re.search(r"\b\d{1,12}+/(\d{1,12}+)\b", cl)
                    if sl_m:
                        p_val = sl_m.group(1)

                if a_val not in articles:
                    articles.append(a_val)
                    paras.append(p_val)

    if not articles:
        single_m = re.search(
            r"\b((?:[Gg]eçici\s{1,4}+|[Ee]k\s{1,4}+|[Mm]ükerrer\s{1,4}+)?" + _ARTICLE_NUMBER + r"(?:/[A-Za-zÇĞİÖŞÜçğıöşü])?)\b",
            body_text,
            re.IGNORECASE,
        )
        if single_m:
            articles = [single_m.group(1).strip()]
            paras = [None]

    if not articles:
        return None, None

    art_str = ", ".join(articles)
    non_null_paras = [p for p in paras if p is not None]
    para_str = non_null_paras[0] if len(non_null_paras) == 1 else None
    return art_str, para_str


_LEAD_DATE_STR = (
    r"(?P<lead_date>"
    r"(?:\d{1,2}[./]\d{1,2}[./]\d{4}|\d{1,2}\s{1,4}+(?:Ocak|Şubat|Subat|Mart|Nisan|Mayıs|Mayis|Haziran|Temmuz|Ağustos|Agustos|Eylül|Eylul|Ekim|Kasım|Kasim|Aralık|Aralik)\s{1,4}+\d{4})"
    r"\s{0,4}+(?:t\s{0,4}+a\s{0,4}+r\s{0,4}+i?\s{0,4}+h(?:l\s{0,4}+i?)?|g\s{0,4}+ü\s{0,4}+n(?:ü)?)\s{1,4}+ve\s{1,4}+"
    r")"
)

_ACRONYMS = r"TCK|TMK|HMK|İİK|IIK|CMK|CMUK|İYUK|IYUK|TTK|VUK|DMK|PVSK|AİHS|TBK"
_TITLE_WORD = r"(?:[A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü0-9/-]{0,64}+|ve|veya|ile|dair|hakkında|için|ilişkin)"
_TITLE_END = r"(?:Kanun[a-zçğıöşü]{0,64}+|KHK[a-zçğıöşü]{0,64}+|Yasa[a-zçğıöşü]{0,64}+|Yönetmeli[a-zçğıöşü]{0,64}+|Tebliğ[a-zçğıöşü]{0,64}+|Kararname[a-zçğıöşü]{0,64}+|Anayasa[a-zçğıöşü]{0,64}+)"

# Law header regex
_LAW_HEADER_STR = (
    r"(?:(?:\b" + _LEAD_DATE_STR + r")|\b)"
    r"(?:"
    r"(?:(?:Mülga\s{1,4}+)?(?P<law_no>\d{1,12}+)\s{1,4}+sayılı\s{1,4}+(?P<law_title_1>" + _up_to(_TITLE_WORD + r"\s{1,4}+", 15) + _TITLE_END + r"|" + _ACRONYMS + r"))|"
    r"(?:(?:Mülga\s{1,4}+)?(?P<law_no_bare>\d{1,12}+)\s{1,4}+sayılı\s{1,4}+(?P<law_title_bare>Kanun[a-zçğıöşü]{0,64}+|Yasa[a-zçğıöşü]{0,64}+))|"
    r"(?P<acronym>" + _ACRONYMS + r")\b|"
    r"(?P<anaphoric>(?:[Aa]ynı|[Bb]u|[Aa]nılan)\s{1,4}+" + _TITLE_END + r")\b|"
    r"(?P<law_title_2>" + _up_to(_TITLE_WORD + r"\s{1,4}+", 15) + _TITLE_END + r")\b|"
    r"(?P<anaph_noun>Yönetmeliğin|Kanunun|Kararnamenin|Tebliğin)\b"
    r")"
    r"(?:\s{0,4}+" + _APOSTROPHE_STR + r"\s{0,4}+[a-zçğıöşü]{1,64}+)?" +
    _up_to(r"\s{0,4}+\([^\)\n]{1,60}\)", 2) +
    r"(?:\s{0,4}+" + _APOSTROPHE_STR + r"\s{0,4}+[a-zçğıöşü]{1,64}+)?"
    r"(?:\s{0,4}+(?:eklenen|değiştirilen)\s{1,4}+)?"
    r"(?:\s{0,4}+[\"“'][^\"”'\n]{1,80}[\"”']\s{1,4}+başlıklı)?"
    r"(?:\s{0,4}+(?:Ek|EK-?)\s{0,4}+[IVX\d]{1,12}+(?:[’'][a-zçğıöşü]{1,64}+)?(?:\s{1,4}+yer\s{1,4}+alan)?\s{0,4}+)?"
)

_PAT_LAW_HEADER = re.compile(_LAW_HEADER_STR)

_ART_ATOM = r"(?:(?:[Gg]eçici|[Ee]k|[Mm]ükerrer|[Mm]ülga)\s{1,4}+)?" + _ARTICLE_NUMBER + r"(?:/[A-Za-zÇĞİÖŞÜçğıöşü\d-]{1,64}+)?"
_ORD_WORD = (
    r"(?:birinci|ikinci|üçüncü|dördüncü|beşinci|altıncı|yedinci|sekizinci|dokuzuncu|onuncu|onbeşinci|ilk|\d{1,12}+)"
    r"(?:\s{0,4}+(?:[ıiuü]?nc[ıiuü]|\.))?"
)
_PARA_STR = (
    r"(?:\s{0,4}+(?:'?[a-zçğıöşü]{1,64}+\s{1,4}+)?" + _ORD_WORD +
    _up_to(r"\s{0,4}+(?:ve|veya|,)\s{0,4}+" + _ORD_WORD, 3) + r"\s{0,4}+f\s{0,4}+ı\s{0,4}+k\s{0,4}+r\s{0,4}+a[a-zçğıöşü]{0,64}+)"
)
_BENT_STR = (
    r"(?:\s{0,4}+\([a-zçğıöşü\d]\)" + _up_to(r"\s{0,4}+(?:ve|veya|,)\s{0,4}+\([a-zçğıöşü\d]\)", 5) + r"\s{0,4}+bend[a-zçğıöşü]{0,64}+|"
    r"\s{0,4}+\([a-zçğıöşü\d]\)(?:\s{1,4}+(?:ve|veya)\s{1,4}+\([a-zçğıöşü\d]\))?)"
)

_ART_UNIT_A = (
    _ART_ATOM + r"(?:\s{0,4}+(?:[ıiuü]?nc[ıiuü]|\.))?" +
    _up_to(r"(?:\s{0,4}+,\s{0,4}+|\s{1,4}+(?:ve|veya|ile|ila|ilâ)\s{1,4}+)" + _ART_ATOM + r"(?:\s{0,4}+(?:[ıiuü]?nc[ıiuü]|\.))?", 10) +
    r"\s{0,4}+madde[a-zçğıöşü]{0,64}+"
    r"(?:" + _PARA_STR + _up_to(r"\s{0,4}+(?:ve|veya|,)\s{0,4}+" + _PARA_STR, 3) + r")?"
    r"(?:" + _BENT_STR + r")?"
)

_ART_UNIT_B = (
    r"m\.\s{0,4}+" + _ART_ATOM +
    _up_to(r"(?:\s{0,4}+,\s{0,4}+|\s{1,4}+(?:ve|veya|ile|ila|ilâ)\s{1,4}+)" + _ART_ATOM, 10) +
    r"(?:" + _PARA_STR + r")?"
    r"(?:" + _BENT_STR + r")?"
)

_ART_UNIT_C = (
    r"[\"“']?(?:[Gg]eçici|[Ee]k|[Mm]ülga)\s{1,4}+madde\s{1,4}+\d{1,12}+[\"”']?" +
    _up_to(r"(?:\s{0,4}+,\s{0,4}+|\s{1,4}+(?:ve|veya|ile|ila|ilâ)\s{1,4}+)\d{1,12}+", 10) +
    r"(?:" + _PARA_STR + r")?"
    r"(?:" + _BENT_STR + r")?"
)

_ARTICLE_CLAUSE_STR = (
    r"(?:" + _ART_UNIT_A + r"|" + _ART_UNIT_B + r"|" + _ART_UNIT_C + r")"
    r"(?:\s{1,4}+(?:ve|veya|ile)\s{1,4}+(?:" + _ART_UNIT_A + r"|" + _ART_UNIT_B + r"|" + _ART_UNIT_C + r"))?"
)

_PAT_ARTICLE_CLAUSE = re.compile(r"\b(?:" + _ARTICLE_CLAUSE_STR + r")")

# Short follow pattern: e.g. 'un 65/1 or 442/3 or 125.
_PAT_SHORT_ARTICLE = re.compile(
    r"^(?:[,\s]{0,4}+" + _APOSTROPHE_STR + r"\s{0,4}+[a-zçğıöşü]{1,64}+)?"
    r"(?:[,\s]{1,4}+)"
    r"(?P<art>\d{1,4})(?:/(?P<para>\d{1,2}))?" +
    _up_to(r"(?:\s{0,4}+,\s{0,4}+|\s{1,4}+(?:ve|veya|ile)\s{1,4}+)\d{1,4}(?:/\d{1,2})?", 5) +
    r"(?:\.|\b)"
)

_PAT_SLASH_SHORT = re.compile(
    r"\b(?P<acronym>" + _ACRONYMS + r")/(?P<art>\d{1,12}+)\b",
    re.IGNORECASE,
)

def extract_citations(text: str) -> List[Dict[str, Any]]:
    items = []
    seen_spans: set[Tuple[int, int]] = set()
    covered = bytearray(len(text))

    # 1. Slash short: TCK/141
    for m in _PAT_SLASH_SHORT.finditer(text):
        start, end = m.start(), m.end()
        span = (start, end)
        seen_spans.add(span)
        covered[start:end] = b"\1" * (end - start)
        groups = m.groupdict()
        items.append({
            "start": start,
            "end": end,
            "text": text[start:end],
            "type": "citation",
            "law_no": None,
            "law_name": groups["acronym"].upper(),
            "article": groups["art"],
            "paragraph": None,
        })

    # Header matches cannot overlap one another, but can overlap slash forms.
    slash_ends = _span_ends(len(text), list(seen_spans))
    # 2. Law headers (and attached articles)
    for m in _PAT_LAW_HEADER.finditer(text):
        h_start, h_end = m.start(), m.end()

        # Check if preceded or followed by Anayasa Mahkemesi (court, not constitution citation)
        full_match_text = text[h_start:h_end]
        if "Anayasa" in full_match_text:
            lookahead = text[h_end:h_end+25].lower()
            if lookahead.startswith(" mahkemesi") or lookahead.startswith("mahkemesi"):
                continue

        groups = m.groupdict()
        law_no = groups.get("law_no") or groups.get("law_no_bare")
        raw_name = (
            groups.get("law_title_1") or
            groups.get("law_title_bare") or
            groups.get("acronym") or
            groups.get("law_title_2")
        )
        anaphoric = groups.get("anaphoric") or groups.get("anaph_noun")

        law_name = _clean_law_name(raw_name) if raw_name else None
        if anaphoric:
            law_name = None
            law_no = None

        # Check for Tebliğ No inside parenthetical: (Tebliğ No: 2024/53)
        if not law_no:
            teb_m = re.search(r"\((?:Tebliğ\s{1,4}+No:\s{0,4}+|Karar\s{1,4}+No:\s{0,4}+)([\w/-]{1,64}+)\)", full_match_text)
            if teb_m:
                law_no = teb_m.group(1)

        # Check what follows immediately after the law header
        # The grammar has a fixed maximum clause length; do not copy the entire
        # remaining document for every header in a reference-heavy chunk.
        after_text = text[h_end:h_end + 8192]
        m_art = _PAT_ARTICLE_CLAUSE.match(after_text.lstrip(" ,\t\r\n"))

        art_span_text = None
        full_span_end = h_end

        if m_art:
            offset = len(after_text) - len(after_text.lstrip(" ,\t\r\n")) + m_art.end()
            full_span_end = h_end + offset
            art_span_text = m_art.group(0)
        else:
            m_short = _PAT_SHORT_ARTICLE.match(after_text)
            if m_short:
                # Ensure it's not a date like 12/9/2010
                look_ahead_short = after_text[m_short.end():m_short.end()+20]
                if not re.match(r"^\s{0,4}+/\d{4}", look_ahead_short) and not re.match(r"^\s{0,4}+tarih", look_ahead_short):
                    full_span_end = h_end + m_short.end()
                    art_span_text = m_short.group(0)

        # Strip trailing punctuation from citation span
        span_str = text[h_start:full_span_end]
        rstrip_len = len(span_str) - len(span_str.rstrip(" ,."))
        full_span_end -= rstrip_len
        span_str = text[h_start:full_span_end]

        if not art_span_text:
            continue

        art_str, para_str = _parse_articles_and_paragraphs(art_span_text)

        span = (h_start, full_span_end)
        if full_span_end <= slash_ends[h_start]:
            continue

        items.append({
            "start": h_start,
            "end": full_span_end,
            "text": span_str,
            "type": "citation",
            "law_no": law_no,
            "law_name": law_name,
            "article": art_str,
            "paragraph": para_str,
        })
        seen_spans.add(span)
        covered[h_start:full_span_end] = b"\1" * (full_span_end - h_start)

    # 3. Standalone article clauses
    for m in _PAT_ARTICLE_CLAUSE.finditer(text):
        start, end = m.start(), m.end()
        span_text = text[start:end]

        rstrip_len = len(span_text) - len(span_text.rstrip(" ,."))
        end -= rstrip_len
        span_text = text[start:end]

        span = (start, end)
        if any(covered[start:end]):
            continue

        art_str, para_str = _parse_articles_and_paragraphs(span_text)
        if not art_str:
            continue

        items.append({
            "start": start,
            "end": end,
            "text": span_text,
            "type": "citation",
            "law_no": None,
            "law_name": None,
            "article": art_str,
            "paragraph": para_str,
        })
        seen_spans.add(span)
        covered[start:end] = b"\1" * (end - start)

    items.sort(key=lambda x: (x["start"], x["end"]))
    return items



# ----------------------------------------------------------------------
# 7. Outcome
# ----------------------------------------------------------------------
_OUTCOME_RULES = [
    ("gönderme", re.compile(r"\b((?:(?:YARGITAY\s{1,4}+)?(?:\d{1,12}+\.\s{1,4}+)?(?:HUKUK|CEZA)\s{1,4}+DA[İI]RES[İI]NE\s{1,4}+GÖNDER[İI]LMES[İI]NE)|(?:(?:Özel\s{1,4}+)?Dairesine\s{1,4}+GER[İI]\s{1,4}+ÇEVR[İI]LMES[İI]NE))\b")),
    ("düzeltilerek onama", re.compile(r"\b(DÜZELT[İI]LEREK\s{1,4}+ONANMASINA)\b")),
    ("kısmen kabul", re.compile(r"\b(KISMEN\s{1,4}+KABUL[ÜU]NE)\b")),
    ("onama", re.compile(r"\b(ONANMASINA)\b")),
    ("bozma", re.compile(r"\b(BOZULMASINA)\b")),
    ("kabul", re.compile(r"\b(KABUL[ÜU]NE)\b")),
    ("red", re.compile(r"\b(REDD[İI]NE)\b")),
    ("direnme", re.compile(r"\b(D[İI]REN[İI]LMES[İI]NE)\b")),
]

_OUTCOME_END = re.compile(r"\bTEVD[İI][İI]NE\b")
_OUTCOME_WORD = re.compile(r"[A-Za-zÇĞİÖŞÜçğıöşü0-9.]{1,64}+")


def _forwarding_spans(text: str) -> Iterator[tuple[int, int]]:
    # Start at a terminator; examine only a bounded preceding word sequence.
    # Retrying every possible prefix of a failed gönderilmek clause was cubic.
    if "gönderilmek" not in text:
        return
    for end in _OUTCOME_END.finditer(text):
        words = []
        pos = end.start()
        for _ in range(26):
            gap_end = pos
            while pos > 0 and gap_end - pos < 4 and text[pos - 1].isspace():
                pos -= 1
            if pos == gap_end:
                break
            word_end = pos
            while pos > 0 and word_end - pos < 64 and (
                text[pos - 1].isalnum() or text[pos - 1] == "."
            ):
                pos -= 1
            word = text[pos:word_end]
            if not _OUTCOME_WORD.fullmatch(word):
                break
            words.append((pos, word))
        words.reverse()
        for i, (_, word) in enumerate(words):
            if word == "gönderilmek" and i + 1 < len(words) and words[i + 1][1] == "üzere":
                if len(words) - i - 2 <= 12:
                    yield words[max(0, i - 12)][0], end.end()
                    break


def extract_outcomes(text: str) -> List[Dict[str, Any]]:
    items = []
    seen_spans: set[Tuple[int, int]] = set()
    for start, end in _forwarding_spans(text):
        items.append({"start": start, "end": end, "text": text[start:end],
                      "type": "outcome", "value": "gönderme"})
        seen_spans.add((start, end))
    for val, pat in _OUTCOME_RULES:
        previous_ends = _span_ends(len(text), list(seen_spans))
        for m in pat.finditer(text):
            span = (m.start(), m.end())
            if m.end() <= previous_ends[m.start()]:
                continue
            items.append({
                "start": m.start(),
                "end": m.end(),
                "text": text[m.start():m.end()],
                "type": "outcome",
                "value": val,
            })
            seen_spans.add(span)
    return items


# ----------------------------------------------------------------------
# Master extractor
# ----------------------------------------------------------------------
def extract_all_rules(text: str) -> List[Dict[str, Any]]:
    """Extract all RULE items from chunk text according to schema.json."""
    decision_refs = extract_decision_refs(text)
    citations = extract_citations(text)
    amounts = extract_amounts(text)
    durations = extract_durations(text)
    outcomes = extract_outcomes(text)
    dates = extract_dates(text)

    # Filter out dates inside citation or decision_ref spans, or inside amendment markers
    if dates:
        ref_spans = [(it["start"], it["end"]) for it in (decision_refs + citations)]
        amendment_marker_spans = []
        markers = {
            m.start() for m in re.finditer(r"\((?:(?:Başlığı\s{1,4}+ile\s{1,4}+birlikte\s{1,4}+)?(?:Değişik|Ek|Mülga|İptal))\b", text, re.IGNORECASE)
        }
        opens: list[int] = []
        for i, char in enumerate(text):
            if char == "(":
                opens.append(i)
            elif char == ")" and opens:
                start = opens.pop()
                if start in markers:
                    amendment_marker_spans.append((start, i + 1))

        suppressed_spans = ref_spans + amendment_marker_spans
        suppressed_ends = _span_ends(len(text), suppressed_spans)

        valid_dates = []
        for d in dates:
            d_start, d_end = d["start"], d["end"]
            if d_end > suppressed_ends[d_start]:
                valid_dates.append(d)
    else:
        valid_dates = []

    candidates = decision_refs + citations + amounts + durations + outcomes + valid_dates
    candidates.sort(key=lambda x: (int(x["start"]), -int(x["end"])))

    deduped = []
    seen_spans: set[Tuple[int, int, str]] = set()
    for it in candidates:
        key = (int(it["start"]), int(it["end"]), str(it["type"]))
        if key in seen_spans:
            continue
        seen_spans.add(key)
        if text[it["start"]:it["end"]] != it["text"]:
            continue
        deduped.append(it)

    deduped.sort(key=lambda x: (int(x["start"]), int(x["end"])))
    return deduped


def extract(text: str) -> List[Dict[str, Any]]:
    """Extract schema-shaped Turkish legal rule items from text.

    Guarantees text[start:end] == item['text'] for every returned item.
    """
    if not text:
        return []
    items = extract_all_rules(text)
    valid_items = []
    for item in items:
        start = item.get("start")
        end = item.get("end")
        if start is not None and end is not None and 0 <= start < end <= len(text):
            if text[start:end] == item.get("text"):
                valid_items.append(item)
    return valid_items
