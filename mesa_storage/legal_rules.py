"""Turkish legal rule-based item extractor for MESA.

Standard library only. Layer 0 (mesa_storage).
Schema: mesa_bench/extract/schema.json v1.3.
Extracts RULE types: citation, decision_ref, date, amount, duration, outcome.
"""

import re
from typing import Any, Dict, List, Optional, Tuple, cast

RULES_VERSION = "tr-legal-rules-2"

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
_NUM_WORD_TOKEN_STR = r"(?:" + _NUM_ROOTS_STR + r"[a-zçğıöşü]*)"
_DURATION_NUM_STR = r"(?:\d+|" + _NUM_WORD_TOKEN_STR + r"(?:\s+" + _NUM_WORD_TOKEN_STR + r")*)"

_DURATION_PAT = re.compile(
    r"\b(?P<num>" + _DURATION_NUM_STR + r")"
    r"(?:(?<=\d)\s*|(?<=[a-zçğıöşü])\s+)"
    r"(?P<unit_full>"
    r"(?P<unit_lemma>i\s*ş\s+g\s*ü\s*n\s*ü|g\s*ü\s*n|h\s*a\s*f\s*t\s*a|a\s*y|y\s*ı\s*l|s\s*a\s*a\s*t|(?:eğitim-öğretim\s+)?y\s*a\s*r\s*ı\s*y\s*ı\s*l)"
    r"(?:[a-zçğıöşü]+)?"
    r")\b",
    re.IGNORECASE,
)

_MONTH_NAMES = {
    "ocak", "şubat", "mart", "nisan", "mayıs", "haziran", "temmuz",
    "ağustos", "eylül", "ekim", "kasım", "aralık"
}


def extract_durations(text: str) -> List[Dict[str, Any]]:
    items = []
    for m in _DURATION_PAT.finditer(text):
        start_pos = m.start()
        # Suppress match if preceded by date like 14.6.1982 gün
        prefix = text[max(0, start_pos - 10):start_pos]
        if re.search(r"\d+[./]\s*$", prefix):
            continue

        num_str = m.group("num").strip()
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

        unit_raw = re.sub(r"\s+", "", m.group("unit_lemma").lower())
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
        sub_num = text[m.start():m.start("unit_full")]
        cand_offset = sub_num.find(cand_str)
        span_start = m.start() + (cand_offset if cand_offset != -1 else 0)
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
_DIGIT_NUM_STR = r"(?:\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?)"

_CURRENCY_PAT_STR = (
    r"(?:"
    r"TL|T\.L\.|₺|"
    r"(?:Türk\s+)?[Ll]\s*i\s*r\s*a\s*s\s*ı[a-zçğıöşü]*|"
    r"(?:Türk\s+)?[Ll]\s*i\s*r\s*a[a-zçğıöşü]*|"
    r"avro|euro|eur|€|"
    r"dolar[a-zçğıöşü]*|ABD\s*Doları[a-zçğıöşü]*|usd|\$"
    r")\b"
)

_DIGIT_AMOUNT_PAT = re.compile(
    r"\b(?P<num>" + _DIGIT_NUM_STR + r")(?:\s*-\s*|\s*)(?P<curr>" + _CURRENCY_PAT_STR + r")",
    re.IGNORECASE,
)

_WORD_AMOUNT_PAT = re.compile(
    r"\b(?P<num>" + _NUM_WORD_TOKEN_STR + r"(?:\s+" + _NUM_WORD_TOKEN_STR + r")*)\s+(?P<curr>"
    + _CURRENCY_PAT_STR
    + r")",
    re.IGNORECASE,
)


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
    for m in _WORD_AMOUNT_PAT.finditer(text):
        num_str = m.group("num").strip()
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

        sub_text = text[m.start():m.end()]
        curr_match = re.search(_CURRENCY_PAT_STR, sub_text, re.IGNORECASE)
        if not curr_match:
            continue
        end = m.start() + curr_match.end()
        cand_idx = sub_text.find(best_words[0])
        if cand_idx == -1:
            continue
        start = m.start() + cand_idx

        # Strip any leading quote characters
        span_str = text[start:end]
        lstrip_len = len(span_str) - len(span_str.lstrip("“\"'‘` "))
        start += lstrip_len
        span_str = text[start:end]

        if any(s <= start and end <= e for s, e in seen):
            continue
        if any(max(s, start) < min(e, end) for s, e in seen):
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
    r"\b(\d{1,2})\s+(Ocak|Şubat|Subat|Mart|Nisan|Mayıs|Mayis|Haziran|Temmuz|Ağustos|Agustos|Eylül|Eylul|Ekim|Kasım|Kasim|Aralık|Aralik)\s+(\d{4})\b",
    re.IGNORECASE,
)
_PARTIAL_DATE_PAT = re.compile(
    r"\b(Ocak|Şubat|Subat|Mart|Nisan|Mayıs|Mayis|Haziran|Temmuz|Ağustos|Agustos|Eylül|Eylul|Ekim|Kasım|Kasim|Aralık|Aralik)\s+(\d{4})\b",
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
    for m in _PARTIAL_DATE_PAT.finditer(text):
        m_start, m_end = m.start(), m.end()
        if any(s <= m_start and m_end <= e for s, e in text_date_spans):
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
_DOCKET_PAT_STR = r"(?:\d{4}/(?:\(\d+\))?[\d-]+|\d{4}/\d+)"
_DECISION_PAT_STR = r"\d{4}/\d+"
_DATE_PAT_STR = r"\d{1,2}[./]\d{1,2}[./]\d{4}"
_DATE_CLAUSE_STR = r"(?P<date_pre>" + _DATE_PAT_STR + r")\s*(?:t\s*a\s*r\s*i?\s*h(?:l\s*i?)?|g\s*ü\s*n)(?:[,\s]+ve|[,\s]+)?\s*"

_LOC_PREFIX_STR = r"(?-i:[A-ZÇĞİÖŞÜ][a-zçğıöşü]+(?:\s+[A-ZÇĞİÖŞÜ][a-zçğıöşü]+)?\s+)"

_COURT_CHAM_PAT_STR = (
    r"(?P<court_cham>"
    r"(?:(?:Yargıtay|Danıştay|Y\.C\.G\.K)\s+)?\s*"
    r"(?:\((?:kapanan|kapatılan)\)\s+)?(?:\d+\.\s+)?(?:Ceza|Hukuk)?\s*(?:Genel\s+Kurulu?[\w]*|Dairesi[\w]*)|"
    r"(?:(?:" + _LOC_PREFIX_STR + r")?\d*\.?\s*)?(?:Asliye\s+(?:Hukuk|Ceza)|İş|Ticaret|Sulh\s+(?:Hukuk|Ceza)|Bölge\s+Adliye|Ağır\s+Ceza)\s+Mahkemes[a-zçğıöşü]*|"
    r"Özel\s+Dairece|Dairece|Mahkemece|Anayasa\s+M\s*a\s*h\s*k?\s*e\s*m\s*e\s*s\s*i[’'\w]*|Y\.C\.G\.K"
    r")\s*(?:verilen\s+)?"
)

# Pattern 1: Docket E., Decision K.
_DEC_REF_PAT1 = re.compile(
    r"(?:" + _COURT_CHAM_PAT_STR + r")?"
    r"(?:" + _DATE_CLAUSE_STR + r")?"
    r"\s*(?P<docket>" + _DOCKET_PAT_STR + r")\s*(?:E\.|Esas)"
    r"[,\s]+"
    r"(?P<decision>" + _DECISION_PAT_STR + r")\s*(?:K\.|Karar)"
    r"(?:[,\s]+(?P<date_post>" + _DATE_PAT_STR + r")\s*(?:t\s*a\s*r\s*i?\s*h(?:l\s*i?)?|g\s*ü\s*n))?",
    re.IGNORECASE,
)

# Pattern 2: Anayasa Mahkemesi style: E. <docket>, K. <decision>
_DEC_REF_PAT2 = re.compile(
    r"(?:" + _COURT_CHAM_PAT_STR + r")?"
    r"(?:" + _DATE_CLAUSE_STR + r")?"
    r"\s*(?:E\.|E\.:|Esas\s+No:?)\s*(?P<docket>" + _DOCKET_PAT_STR + r")"
    r"[,\s]+"
    r"(?:K\.|K\.:|Karar\s+No:?)\s*(?P<decision>" + _DECISION_PAT_STR + r")",
    re.IGNORECASE,
)

# Pattern 3: Single K pattern (e.g. 'Y.C.G.K 2020/527 K')
_DEC_REF_PAT3 = re.compile(
    r"(?P<court_cham>Y\.C\.G\.K)\s+(?P<decision>\d{4}/\d+)\s*K\b",
    re.IGNORECASE,
)

# Pattern 4: Short form <court/chamber>? <date> tarih(li) ve <n>-<m> (sayılı|sayı)?
_DEC_REF_PAT4 = re.compile(
    r"(?:(?P<court_cham>"
    r"(?:(?:(?:" + _LOC_PREFIX_STR + r")?Bölge\s+Adliye\s+Mahkemesi|Yargıtay|Danıştay|Y\.C\.G\.K)\s+)?\s*"
    r"(?:\((?:kapanan|kapatılan)\)\s+)?(?:\d+\.\s+)?(?:Ceza|Hukuk)?\s*(?:Genel\s+Kurulu?[\w]*|Dairesi[\w]*)|"
    r"(?:(?:" + _LOC_PREFIX_STR + r")?\d*\.?\s*)?(?:Asliye\s+(?:Hukuk|Ceza)|İş|Ticaret|Sulh\s+(?:Hukuk|Ceza)|Bölge\s+Adliye|Ağır\s+Ceza)\s+Mahkemes[a-zçğıöşü]*|"
    r"Özel\s+Dairece|Dairece|Mahkemece|Anayasa\s+M\s*a\s*h\s*k?\s*e\s*m\s*e\s*s\s*i[’'\w]*|Y\.C\.G\.K"
    r")\s*(?:verilen\s+)?)?"
    r"(?P<date>" + _DATE_PAT_STR + r")\s*(?:t\s*a\s*r\s*i?\s*h(?:l\s*i?)?|g\s*ü\s*n)(?:[,\s]+ve|[,\s]+)\s*"
    r"(?P<docket>\d+)\s*-\s*(?P<decision>\d+)(?:\s*(?:sayılı|sayı))?",
    re.IGNORECASE,
)

# Pattern 5: Single Esas with court (e.g. 'Bandırma Sulh Hukuk Mahkemesinde görülen 2009/183 Esas')
_DEC_REF_PAT5 = re.compile(
    r"(?P<court_cham>"
    r"(?:(?:" + _LOC_PREFIX_STR + r")?\d*\.?\s*)?(?:Asliye\s+(?:Hukuk|Ceza)|İş|Ticaret|Sulh\s+(?:Hukuk|Ceza)|Bölge\s+Adliye|Ağır\s+Ceza)\s+Mahkemes[a-zçğıöşü]*|"
    r"(?:Yargıtay|Danıştay)\s+(?:\d+\.\s+)?(?:Ceza|Hukuk)\s+Dairesi[\w]*"
    r")\s*(?:verilen\s+|kayıtlı\s+|görülen\s+)(?P<docket>\d{4}/\d+)\s*(?:E\.|Esas)\b(?!\s*,\s*\d{4}/\d+\s*(?:K\.|Karar))",
    re.IGNORECASE,
)


def _clean_court_chamber(raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Split and clean court and chamber names from matched court_cham text."""
    if not raw:
        return None, None
    raw_str = re.sub(r"\s+", " ", raw.strip())
    raw_str = re.sub(r"\((?:kapatılan|kapanan)\)\s*", "", raw_str, flags=re.IGNORECASE).strip()
    raw_str = re.sub(r"[’'\x27\u2032][a-zçğıöşü]+$", "", raw_str, flags=re.IGNORECASE).strip()
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
        m = re.match(r"^(.*?Bölge\s+Adliye\s+Mahkemesi)\s*(.*)$", raw_str, re.IGNORECASE)
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


def extract_decision_refs(text: str) -> List[Dict[str, Any]]:
    if "/" not in text and "-" not in text:
        return []
    items = []
    seen_spans: set[Tuple[int, int]] = set()

    for pat in [_DEC_REF_PAT2, _DEC_REF_PAT1, _DEC_REF_PAT4, _DEC_REF_PAT5, _DEC_REF_PAT3]:
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
            if any(s <= span[0] and span[1] <= e for s, e in seen_spans):
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
    header = re.sub(r"\s+", " ", header.strip())
    header = re.sub(r"^Mülga\s+", "", header)
    header = re.sub(r"\s*\([^\)\n]{1,60}\)\s*", " ", header).strip()
    header = re.sub(r"[’'\x27\u2032\u2018\u201b]\s*[a-zçğıöşü]+$", "", header).strip()

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
        m_range = re.search(r"geçici\s+(\d+)\s+il[aâ]\s+geçici\s+(\d+)", body_text, re.IGNORECASE)
        if m_range:
            start_n, end_n = int(m_range.group(1)), int(m_range.group(2))
            arts = [f"geçici {i}" for i in range(start_n, end_n + 1)]
            return ", ".join(arts), None

        m_gm = re.search(r"Geçici\s+madde\s+(\d+)", body_text, re.IGNORECASE)
        if m_gm:
            return f"Geçici {m_gm.group(1)}", None

    if "il" in lower:
        m_ila = re.search(r"\b(\d+)\s+il[aâ]\s+(\d+)\s*(?:[ıiuü]?nc[ıiuü]|\.)?\s*madde", body_text, re.IGNORECASE)
        if m_ila:
            return f"{m_ila.group(1)}, {m_ila.group(2)}", None

    if "tbk" in lower:
        m_tbk = re.search(r"\bTBK/(\d+)\b", body_text)
        if m_tbk:
            return m_tbk.group(1), None

    articles = []
    paras = []

    m_list = re.findall(r"\bm\.\s*(\d+(?:\.\d+)*)(?:/(\d+))?", body_text)
    if len(m_list) >= 2:
        for a, p in m_list:
            articles.append(a)
            paras.append(p if p else None)
    else:
        clauses = re.split(r"(?:,\s*|\s+(?:ve|veya|ile|ilâ|ila)\s+)", body_text)
        for cl in clauses:
            cl = cl.strip()
            if not cl:
                continue
            art_m = re.search(
                r"\b(?:m\.\s*)?((?:[Gg]eçici\s+|[Ee]k\s+|[Mm]ükerrer\s+|[Mm]ülga\s+)?\d+(?:\.\d+)*(?:/[A-Za-zÇĞİÖŞÜçğıöşü])?)(?:/(\d+))?",
                cl,
                re.IGNORECASE,
            )
            if art_m:
                a_val = re.sub(r"\s+", " ", art_m.group(1).strip())
                if a_val.lower().startswith("mükerrer "):
                    a_val = "mükerrer " + a_val.split()[1]
                p_val = art_m.group(2)
                if not p_val:
                    pw_m = re.search(
                        r"\b(birinci|ikinci|üçüncü|dördüncü|beşinci|altıncı|yedinci|sekizinci|dokuzuncu|onuncu|onbeşinci|\d+)\s*(?:[ıiuü]?nc[ıiuü]|\.)?\s*f\s*ı\s*k\s*r\s*a",
                        cl,
                        re.IGNORECASE,
                    )
                    if pw_m:
                        w = pw_m.group(1).lower()
                        p_val = _ORDINAL_MAP.get(w, w)
                if not p_val:
                    sl_m = re.search(r"\b\d+/(\d+)\b", cl)
                    if sl_m:
                        p_val = sl_m.group(1)

                if a_val not in articles:
                    articles.append(a_val)
                    paras.append(p_val)

    if not articles:
        single_m = re.search(
            r"\b((?:[Gg]eçici\s+|[Ee]k\s+|[Mm]ükerrer\s+)?\d+(?:\.\d+)*(?:/[A-Za-zÇĞİÖŞÜçğıöşü])?)\b",
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
    r"(?:\d{1,2}[./]\d{1,2}[./]\d{4}|\d{1,2}\s+(?:Ocak|Şubat|Subat|Mart|Nisan|Mayıs|Mayis|Haziran|Temmuz|Ağustos|Agustos|Eylül|Eylul|Ekim|Kasım|Kasim|Aralık|Aralik)\s+\d{4})"
    r"\s*(?:\r?\n\s*)?(?:t\s*a\s*r\s*i?\s*h(?:l\s*i?)?|g\s*ü\s*n(?:ü)?)\s+ve\s+"
    r")"
)

_ACRONYMS = r"TCK|TMK|HMK|İİK|IIK|CMK|CMUK|İYUK|IYUK|TTK|VUK|DMK|PVSK|AİHS|TBK"
_TITLE_WORD = r"(?:[A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü0-9/-]*|ve|veya|ile|dair|hakkında|için|ilişkin)"
_TITLE_END = r"(?:Kanun[a-zçğıöşü]*|KHK[a-zçğıöşü]*|Yasa[a-zçğıöşü]*|Yönetmeli[a-zçğıöşü]*|Tebliğ[a-zçğıöşü]*|Kararname[a-zçğıöşü]*|Anayasa[a-zçğıöşü]*)"

# Law header regex
_LAW_HEADER_STR = (
    r"(?:(?:\b" + _LEAD_DATE_STR + r")|\b)"
    r"(?:"
    r"(?:(?:Mülga\s+)?(?P<law_no>\d+)\s+sayılı\s+(?P<law_title_1>(?:" + _TITLE_WORD + r"\s+){0,15}" + _TITLE_END + r"|" + _ACRONYMS + r"))|"
    r"(?:(?:Mülga\s+)?(?P<law_no_bare>\d+)\s+sayılı\s+(?P<law_title_bare>Kanun[a-zçğıöşü]*|Yasa[a-zçğıöşü]*))|"
    r"(?P<acronym>" + _ACRONYMS + r")\b|"
    r"(?P<anaphoric>(?:[Aa]ynı|[Bb]u|[Aa]nılan)\s+" + _TITLE_END + r")\b|"
    r"(?P<law_title_2>(?:" + _TITLE_WORD + r"\s+){0,15}" + _TITLE_END + r")\b|"
    r"(?P<anaph_noun>Yönetmeliğin|Kanunun|Kararnamenin|Tebliğin)\b"
    r")"
    r"(?:\s*" + _APOSTROPHE_STR + r"\s*[a-zçğıöşü]+)?"
    r"(?:\s*\([^\)\n]{1,60}\)){0,2}"
    r"(?:\s*" + _APOSTROPHE_STR + r"\s*[a-zçğıöşü]+)?"
    r"(?:\s*(?:eklenen|değiştirilen)\s+)?"
    r"(?:\s*[\"“'][^\"”'\n]{1,80}[\"”']\s+başlıklı)?"
    r"(?:\s*(?:Ek|EK-?)\s*[IVX\d]+(?:[’'][a-zçğıöşü]+)?(?:\s+yer\s+alan)?\s*)?"
)

_PAT_LAW_HEADER = re.compile(_LAW_HEADER_STR)

_ART_ATOM = r"(?:(?:[Gg]eçici|[Ee]k|[Mm]ükerrer|[Mm]ülga)\s+)?\d+(?:\.\d+)*(?:/[A-Za-zÇĞİÖŞÜçğıöşü\d-]+)?"
_ORD_WORD = (
    r"(?:birinci|ikinci|üçüncü|dördüncü|beşinci|altıncı|yedinci|sekizinci|dokuzuncu|onuncu|onbeşinci|ilk|\d+)"
    r"(?:\s*(?:[ıiuü]?nc[ıiuü]|\.))?"
)
_PARA_STR = (
    r"(?:\s*(?:'?[a-zçğıöşü]+\s+)?" + _ORD_WORD +
    r"(?:\s*(?:ve|veya|,)\s*" + _ORD_WORD + r"){0,3}\s*f\s*ı\s*k\s*r\s*a[a-zçğıöşü]*)"
)
_BENT_STR = (
    r"(?:\s*\([a-zçğıöşü\d]\)(?:\s*(?:ve|veya|,)\s*\([a-zçğıöşü\d]\))*\s*bend[a-zçğıöşü]*|"
    r"\s*\([a-zçğıöşü\d]\)(?:\s+(?:ve|veya)\s+\([a-zçğıöşü\d]\))?)"
)

_ART_UNIT_A = (
    _ART_ATOM + r"(?:\s*(?:[ıiuü]?nc[ıiuü]|\.))?"
    r"(?:\s*(?:,\s*|\s+(?:ve|veya|ile|ila|ilâ)\s+)" + _ART_ATOM + r"(?:\s*(?:[ıiuü]?nc[ıiuü]|\.))?){0,10}"
    r"\s*madde[a-zçğıöşü]*"
    r"(?:" + _PARA_STR + r"(?:\s*(?:ve|veya|,)\s*" + _PARA_STR + r"){0,3})?"
    r"(?:" + _BENT_STR + r")?"
)

_ART_UNIT_B = (
    r"m\.\s*" + _ART_ATOM +
    r"(?:\s*(?:,\s*|\s+(?:ve|veya|ile|ila|ilâ)\s+)" + _ART_ATOM + r"){0,10}"
    r"(?:" + _PARA_STR + r")?"
    r"(?:" + _BENT_STR + r")?"
)

_ART_UNIT_C = (
    r"[\"“']?(?:[Gg]eçici|[Ee]k|[Mm]ülga)\s+madde\s+\d+[\"”']?"
    r"(?:\s*(?:,\s*|\s+(?:ve|veya|ile|ila|ilâ)\s+)\d+){0,10}"
    r"(?:" + _PARA_STR + r")?"
    r"(?:" + _BENT_STR + r")?"
)

_ARTICLE_CLAUSE_STR = (
    r"(?:" + _ART_UNIT_A + r"|" + _ART_UNIT_B + r"|" + _ART_UNIT_C + r")"
    r"(?:\s+(?:ve|veya|ile)\s+(?:" + _ART_UNIT_A + r"|" + _ART_UNIT_B + r"|" + _ART_UNIT_C + r"))?"
)

_PAT_ARTICLE_CLAUSE = re.compile(r"\b(?:" + _ARTICLE_CLAUSE_STR + r")")

# Short follow pattern: e.g. 'un 65/1 or 442/3 or 125.
_PAT_SHORT_ARTICLE = re.compile(
    r"^(?:[,\s]*" + _APOSTROPHE_STR + r"\s*[a-zçğıöşü]+)?"
    r"(?:[,\s]+)"
    r"(?P<art>\d{1,4})(?:/(?P<para>\d{1,2}))?"
    r"(?:\s*(?:,\s*|\s+(?:ve|veya|ile)\s+)\d{1,4}(?:/\d{1,2})?){0,5}"
    r"(?:\.|\b)"
)

_PAT_SLASH_SHORT = re.compile(
    r"\b(?P<acronym>" + _ACRONYMS + r")/(?P<art>\d+)\b",
    re.IGNORECASE,
)

def extract_citations(text: str) -> List[Dict[str, Any]]:
    items = []
    seen_spans: set[Tuple[int, int]] = set()

    # 1. Slash short: TCK/141
    for m in _PAT_SLASH_SHORT.finditer(text):
        start, end = m.start(), m.end()
        span = (start, end)
        seen_spans.add(span)
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
            teb_m = re.search(r"\((?:Tebliğ\s+No:\s*|Karar\s+No:\s*)([\w/-]+)\)", full_match_text)
            if teb_m:
                law_no = teb_m.group(1)

        # Check what follows immediately after the law header
        after_text = text[h_end:]
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
                if not re.match(r"^\s*/\d{4}", look_ahead_short) and not re.match(r"^\s*tarih", look_ahead_short):
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
        if any(s <= span[0] and span[1] <= e for s, e in seen_spans):
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

    # 3. Standalone article clauses
    for m in _PAT_ARTICLE_CLAUSE.finditer(text):
        start, end = m.start(), m.end()
        span_text = text[start:end]

        rstrip_len = len(span_text) - len(span_text.rstrip(" ,."))
        end -= rstrip_len
        span_text = text[start:end]

        span = (start, end)
        if any(s <= span[0] and span[1] <= e for s, e in seen_spans):
            continue
        if any(max(s, span[0]) < min(e, span[1]) for s, e in seen_spans):
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

    items.sort(key=lambda x: (x["start"], x["end"]))
    return items



# ----------------------------------------------------------------------
# 7. Outcome
# ----------------------------------------------------------------------
_OUTCOME_RULES = [
    ("gönderme", re.compile(r"\b((?:(?:[A-Za-zÇĞİÖŞÜçğıöşü0-9.]+\s+)*gönderilmek\s+üzere\s+(?:[A-Za-zÇĞİÖŞÜçğıöşü0-9.]+\s+)*TEVD[İI][İI]NE)|(?:(?:YARGITAY\s+)?(?:\d+\.\s+)?(?:HUKUK|CEZA)\s+DA[İI]RES[İI]NE\s+GÖNDER[İI]LMES[İI]NE)|(?:(?:Özel\s+)?Dairesine\s+GER[İI]\s+ÇEVR[İI]LMES[İI]NE))\b")),
    ("düzeltilerek onama", re.compile(r"\b(DÜZELT[İI]LEREK\s+ONANMASINA)\b")),
    ("kısmen kabul", re.compile(r"\b(KISMEN\s+KABUL[ÜU]NE)\b")),
    ("onama", re.compile(r"\b(ONANMASINA)\b")),
    ("bozma", re.compile(r"\b(BOZULMASINA)\b")),
    ("kabul", re.compile(r"\b(KABUL[ÜU]NE)\b")),
    ("red", re.compile(r"\b(REDD[İI]NE)\b")),
    ("direnme", re.compile(r"\b(D[İI]REN[İI]LMES[İI]NE)\b")),
]


def extract_outcomes(text: str) -> List[Dict[str, Any]]:
    items = []
    seen_spans: set[Tuple[int, int]] = set()
    for val, pat in _OUTCOME_RULES:
        for m in pat.finditer(text):
            span = (m.start(), m.end())
            if any(s <= span[0] and span[1] <= e for s, e in seen_spans):
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
        for m in re.finditer(r"\((?:(?:Başlığı\s+ile\s+birlikte\s+)?(?:Değişik|Ek|Mülga|İptal))\b", text, re.IGNORECASE):
            start = m.start()
            depth = 0
            end = -1
            for i in range(start, len(text)):
                if text[i] == "(":
                    depth += 1
                elif text[i] == ")":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end != -1:
                amendment_marker_spans.append((start, end))

        suppressed_spans = ref_spans + amendment_marker_spans

        valid_dates = []
        for d in dates:
            d_start, d_end = d["start"], d["end"]
            if not any(s <= d_start and d_end <= e for s, e in suppressed_spans):
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
