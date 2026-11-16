"""Parsing, normalisation, and reconciliation of reads.

This module is the deterministic half of pairseal: it turns vision
models' raw replies into parsed reads (fields + line items,
normalised per the document type's schema in pairseal.doctype),
resolves printed money to cents (or refuses to, when the format is
ambiguous), parses printed validity windows and written-out check
amounts, and reconciles two reads field by field and item by item.
No model judgement happens here — agreement is computed, never
negotiated.

Money is handled as integer cents everywhere after parsing. A read
reports both a numeric value and the value's text verbatim as
printed; the verbatim text governs, because document typography is
where values go to be ambiguous ("599" can be $599, $5.99, or 599
cents) and a numeric field alone would let a model silently pick
one of those readings.
"""

from __future__ import annotations

import json
import re
from datetime import date

from .doctype import schema_for

# ------------------------------------------------------------ prompts

_BASE_RULES = """You are a field-extraction reader for a document image.
Extract EVERY field and line item you can actually see. Rules:
- Report a value ONLY if you can read it clearly in the image. Never guess, infer, or complete a partially visible digit or word.
- For money values, transcribe the text EXACTLY as printed (e.g. "5.99", "$5 99", "599") in the value_text field, and your numeric reading of it in the value field.
- If the image is unreadable or has no legible content, return empty fields/items and set "unreadable": true.
- Transcribe non-English text and add an English translation in parentheses.
Return ONLY a JSON object, no prose, no markdown fences."""

_TYPE_BLOCKS = {
    "flyer": """This is a retail flyer, shelf label, or handwritten price sign.
Return: {"doc_type": "flyer", "unreadable": false, "validity_text": "<any printed sale-validity dates, verbatim, or null>",
 "fields": {},
 "items": [{"name": "<product name incl. brand>", "value": <price, dollars>, "value_text": "<price exactly as printed>", "unit": "<size/unit as printed, or null>", "qty": null}]}""",
    "invoice": """This is an invoice.
Fields: vendor, invoice_number, invoice_date, tax, total.
Return: {"doc_type": "invoice", "unreadable": false, "validity_text": null,
 "fields": {"vendor": {"value": "<text>", "value_text": "<as printed>"}, "invoice_number": {"value": "<text>", "value_text": "<as printed>"}, "invoice_date": {"value": "<date as printed>", "value_text": "<as printed>"}, "tax": {"value": <number>, "value_text": "<as printed>"}, "total": {"value": <number>, "value_text": "<as printed>"}},
 "items": [{"name": "<line item description>", "value": <line amount, dollars>, "value_text": "<as printed>", "unit": null, "qty": <quantity or null>}]}""",
    "receipt": """This is a receipt.
Fields: merchant, receipt_date, total.
Return: {"doc_type": "receipt", "unreadable": false, "validity_text": null,
 "fields": {"merchant": {"value": "<text>", "value_text": "<as printed>"}, "receipt_date": {"value": "<date as printed>", "value_text": "<as printed>"}, "total": {"value": <number>, "value_text": "<as printed>"}},
 "items": [{"name": "<item name>", "value": <item price, dollars>, "value_text": "<as printed>", "unit": null, "qty": <quantity or null>}]}""",
    "check": """This is a bank check.
Fields: payee, check_date, amount_numeric (the amount in digits), amount_written (the amount written out in words, transcribed verbatim), routing_number, account_number, memo.
Return: {"doc_type": "check", "unreadable": false, "validity_text": null,
 "fields": {"payee": {"value": "<text>", "value_text": "<as printed>"}, "check_date": {"value": "<date as printed>", "value_text": "<as printed>"}, "amount_numeric": {"value": <number>, "value_text": "<digits as printed>"}, "amount_written": {"value": "<words exactly as written>", "value_text": "<words exactly as written>"}, "routing_number": {"value": "<digits>", "value_text": "<as printed>"}, "account_number": {"value": "<digits>", "value_text": "<as printed>"}, "memo": {"value": "<text>", "value_text": "<as printed>"}},
 "items": []}""",
}


def extraction_prompt(doc_type: str,
                      generic_fields: list[str] | None = None) -> str:
    """The prompt a reader receives. Both readers of a pair get the
    identical prompt; the reads differ only in the model answering.
    For doc_type='auto' the model is asked to classify first and
    then follow the matching block."""
    if doc_type == "generic":
        names = ", ".join(generic_fields or [])
        block = ("Extract these user-named fields: " + names + ".\n"
                 "Return: {\"doc_type\": \"generic\", \"unreadable\": false, "
                 "\"validity_text\": null, \"fields\": {\"<field>\": "
                 "{\"value\": <value>, \"value_text\": \"<as printed>\"}}, "
                 "\"items\": []}")
        return _BASE_RULES + "\n" + block
    if doc_type == "auto":
        blocks = "\n\n".join(_TYPE_BLOCKS[t] for t in
                             ("flyer", "invoice", "receipt", "check"))
        return (_BASE_RULES + "\nFirst decide which document type "
                "this image is, then follow that type's block "
                "exactly.\n\n" + blocks)
    return _BASE_RULES + "\n" + _TYPE_BLOCKS[doc_type]


# ------------------------------------------------------------ parsing

def _money_from_entry(entry: dict) -> tuple[int | None, bool]:
    """A money value from a read entry: (cents, ambiguous)."""
    if not isinstance(entry, dict):
        return None, False
    return resolve_price(entry.get("value"),
                         entry.get("value_text"))


def parse_read(raw: str | None, doc_type: str,
               generic_fields: list[str] | None = None) -> dict | None:
    """One model's raw reply -> a normalised read, or None when the
    reply is unusable (malformed JSON, wrong shape). None is the
    unreadable outcome: a read that cannot be parsed confirms
    nothing.

    A usable read: {"doc_type": str, "unreadable": bool,
    "validity_text": str|None,
    "fields": {name: {"value": <normalised>, "raw": <as reported>,
                      "ambiguous": bool}},
    "items": [{"name", "price_cents": int|None, "ambiguous": bool,
               "unit", "qty"}]}.
    Normalised values: money -> cents int; written_amount -> cents
    int (via words); date -> ISO string; number -> float; text ->
    the string as reported (comparison normalises later)."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    read_type = str(data.get("doc_type") or doc_type)
    if read_type not in ("flyer", "invoice", "receipt", "check",
                         "generic"):
        read_type = doc_type
    effective = read_type if doc_type == "auto" else doc_type
    schema = schema_for(effective, generic_fields)
    if data.get("unreadable"):
        return {"doc_type": effective, "unreadable": True,
                "validity_text": None, "fields": {}, "items": []}
    fields: dict[str, dict] = {}
    raw_fields = data.get("fields") or {}
    if not isinstance(raw_fields, dict):
        return None
    for name, kind in (schema.get("fields") or {}).items():
        if name not in raw_fields:
            continue
        entry = raw_fields[name]
        if not isinstance(entry, dict):
            entry = {"value": entry, "value_text": None}
        value, ambiguous = _normalise_field(kind, entry)
        if value is None and not ambiguous:
            continue  # no usable evidence for this field
        fields[name] = {"value": value, "raw": entry.get("value"),
                        "ambiguous": ambiguous}
    items = []
    if schema.get("items"):
        for entry in data.get("items") or []:
            if not isinstance(entry, dict):
                return None
            name = str(entry.get("name") or "").strip()
            if not name:
                return None
            cents, ambiguous = _money_from_entry(entry)
            if cents is None and not ambiguous:
                return None
            if cents is not None and cents <= 0:
                continue  # a placeholder is not a price
            qty = entry.get("qty")
            items.append({
                "name": name, "price_cents": cents,
                "ambiguous": ambiguous,
                "unit": (str(entry["unit"]).strip()
                         if entry.get("unit") else None),
                "qty": (float(qty) if isinstance(qty, (int, float))
                        and not isinstance(qty, bool) else None),
            })
    validity = data.get("validity_text")
    # Not flagged unreadable by the model: the read succeeded,
    # even if it found nothing (that outcome is no_price_found at
    # the gate, not unreadable).
    return {"doc_type": effective, "unreadable": False,
            "validity_text": str(validity) if validity else None,
            "fields": fields, "items": items}


def _normalise_field(kind: str,
                     entry: dict) -> tuple[object, bool]:
    """(normalised value, ambiguous) for one field entry."""
    if kind == "money":
        return _money_from_entry(entry)
    if kind == "written_amount":
        words = entry.get("value")
        cents = written_amount_to_cents(
            words if isinstance(words, str) else None)
        if cents is None:
            return None, True  # words we cannot resolve are
            # ambiguous, never guessed
        return cents, False
    if kind == "date":
        raw = entry.get("value")
        d = parse_date_value(raw if isinstance(raw, str) else None)
        return (d.isoformat() if d else None), False
    if kind == "number":
        v = entry.get("value")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v), False
        return None, False
    # text
    v = entry.get("value")
    if v is None:
        return None, False
    return str(v), False


# ------------------------------------------------- price normalising

_BARE_INT = re.compile(r"^(\d+)$")
_DECIMAL = re.compile(r"^(\d+)[.,](\d{1,2})$")
_SPACED = re.compile(r"^(\d+)\s+(\d{2})$")
_SLASH = re.compile(r"^(\d+)/(\d{2})$")


def price_text_to_cents(text) -> tuple[int | None, bool]:
    """Printed price text -> (cents, ambiguous).

    Unambiguous: "5.99", "$5.99", "5.9" (= 5.90), "$5 99" (the
    classic flyer superscript-cents layout), "5/99".
    Ambiguous: a bare integer of 3+ digits ("599" — $599, $5.99 and
    599 cents are all live readings; flyer convention makes $5.99
    likely, but likely is not read), and anything with more than
    two numeric groups ("599 99 99" — the garble signature).
    A bare 1–2 digit integer is whole dollars ("5" = $5.00)."""
    if text is None:
        return None, False
    t = str(text).strip().replace("$", "").replace(",", "").strip()
    if not t:
        return None, False
    m = _DECIMAL.match(t)
    if m:
        return int(m.group(1)) * 100 + int(m.group(2).ljust(2, "0")), False
    m = _SPACED.match(t) or _SLASH.match(t)
    if m:
        return int(m.group(1)) * 100 + int(m.group(2)), False
    m = _BARE_INT.match(t)
    if m:
        digits = m.group(1)
        if len(digits) >= 3:
            return None, True
        return int(digits) * 100, False
    return None, True  # numeric content we cannot resolve is
    # ambiguous, never guessed


def resolve_price(number, price_text) -> tuple[int | None, bool]:
    """Combine a numeric value and its verbatim text. The verbatim
    text governs when present: it is the evidence. If the text
    resolves and the number disagrees, the value is ambiguous (the
    model contradicted its own transcription)."""
    text_cents, text_ambiguous = (None, False)
    if price_text is not None and str(price_text).strip():
        text_cents, text_ambiguous = price_text_to_cents(price_text)
    num_cents = None
    if isinstance(number, (int, float)) and not isinstance(number, bool):
        num_cents = int(round(float(number) * 100))
    if text_ambiguous:
        return None, True
    if text_cents is not None:
        if num_cents is not None and num_cents != text_cents:
            return None, True
        return text_cents, False
    if num_cents is not None:
        return num_cents, False
    return None, False


# ------------------------------------------- written amount (checks)

_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}


def _words_to_int(words: list[str]) -> int | None:
    total, current = 0, 0
    seen = False
    for w in words:
        if w in _ONES:
            current += _ONES[w]
            seen = True
        elif w in _TENS:
            current += _TENS[w]
            seen = True
        elif w == "hundred":
            if current == 0:
                return None
            current *= 100
            seen = True
        elif w == "thousand":
            if current == 0:
                return None
            total += current * 1000
            current = 0
            seen = True
        elif w == "and":
            continue
        else:
            return None
    return (total + current) if seen else None


def written_amount_to_cents(text: str | None) -> int | None:
    """A check's written amount -> cents, or None when the words
    cannot be resolved. Handles the standard legal line:
    "Two hundred fifty and 00/100", "One thousand two hundred
    thirty-four and 56/100 dollars", "Seventy-five cents"."""
    if not text:
        return None
    t = str(text).lower().replace("-", " ").replace(",", " ")
    t = re.sub(r"\bdollars?\b", " ", t)
    m = re.search(r"(\d{1,2})\s*/\s*100", t)
    if m:
        cents = int(m.group(1))
        words = [w for w in re.split(r"\s+", t[:m.start()].strip())
                 if w]
        dollars = _words_to_int(words) if words else 0
        return None if dollars is None else dollars * 100 + cents
    m = re.search(r"(\d{1,2})\s*cents?\b", t)
    if m:  # digit-form cents: "50 cents", "and 50 cents"
        cents = int(m.group(1))
        words = [w for w in re.split(
            r"\s+", (t[:m.start()] + t[m.end():]).strip()) if w]
        dollars = _words_to_int(words) if words else 0
        return None if dollars is None else dollars * 100 + cents
    if re.search(r"\bcents?\b", t):
        # Word-form cents. Shape 1: the whole amount is cents
        # ("fifty cents"). Shape 2: dollars and cents in words
        # ("two dollars and fifty cents"). Anything else that
        # mentions cents is unresolvable rather than misread as
        # dollars.
        body = re.sub(r"\bonly\b", " ", t).strip()
        m1 = re.fullmatch(r"([a-z ]+?)\s+cents?", body)
        if m1:
            value = _words_to_int(m1.group(1).split())
            return value if (value is not None and value < 100) \
                else None
        if " and " in body:
            left, _, right = body.rpartition(" and ")
            m2 = re.fullmatch(r"([a-z ]+?)\s+cents?", right.strip())
            if m2:
                cents = _words_to_int(m2.group(1).split())
                dollars = _words_to_int(
                    [w for w in left.split() if w != "and"])
                if cents is not None and dollars is not None:
                    return dollars * 100 + cents
        return None
    words = [w for w in re.split(r"\s+", t.strip()) if w]
    if not words:
        return None
    dollars = _words_to_int(words)
    return None if dollars is None else dollars * 100


# --------------------------------------------------- window parsing

_MONTHS = {name: i + 1 for i, name in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}
_NUM_DATE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_NAME_DATE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
    r"[a-z]*\.?[\s-]+(\d{1,2})(?:[\s,-]+(\d{2,4}))?\b",
    re.IGNORECASE)
_NAME_DATE_REV = re.compile(
    r"\b(\d{1,2})[\s-]+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
    r"[a-z]*\.?(?:[\s,-]+(\d{2,4}))?\b", re.IGNORECASE)


def _mk(year: int, month: int, day: int) -> date | None:
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _dates_in(text: str) -> list[date]:
    found: list[date] = []
    for m in _ISO_DATE.finditer(text):
        d = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            found.append(d)
    for m in _NUM_DATE.finditer(text):
        d = _mk(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        if d:
            found.append(d)
    year_hint = found[0].year if found else None
    for m in _NAME_DATE.finditer(text):
        month = _MONTHS[m.group(1).lower()[:3]]
        year = int(m.group(3)) if m.group(3) else year_hint
        if year:
            d = _mk(year, month, int(m.group(2)))
            if d:
                found.append(d)
    for m in _NAME_DATE_REV.finditer(text):
        month = _MONTHS[m.group(2).lower()[:3]]
        year = int(m.group(3)) if m.group(3) else year_hint
        if year:
            d = _mk(year, month, int(m.group(1)))
            if d:
                found.append(d)
    return found


def parse_date_value(text: str | None) -> date | None:
    """A single date from a field value, or None."""
    if not text:
        return None
    found = _dates_in(str(text))
    return found[0] if found else None


def parse_window(validity_text: str | None) -> dict:
    """Printed validity text -> {"starts_on", "ends_on"} dates:
    earliest to latest named date; one date is a one-day window;
    no dates -> both None (an undated price carries no window)."""
    if not validity_text:
        return {"starts_on": None, "ends_on": None}
    found = _dates_in(str(validity_text))
    if not found:
        return {"starts_on": None, "ends_on": None}
    return {"starts_on": min(found), "ends_on": max(found)}


def merge_windows(text_a: str | None, text_b: str | None) -> dict:
    """The window the gate judges by: the agreed window when both
    reads printed one; the single printed one otherwise; on
    conflict, the NARROWER window (conservative for an expiry
    gate) plus a conflict flag."""
    wa = parse_window(text_a)
    wb = parse_window(text_b)
    if wa["ends_on"] is None:
        return {**wb, "text": text_b or text_a, "conflict": False}
    if wb["ends_on"] is None:
        return {**wa, "text": text_a or text_b, "conflict": False}
    if wa == wb:
        return {**wa, "text": text_a, "conflict": False}
    return {"starts_on": max(wa["starts_on"], wb["starts_on"]),
            "ends_on": min(wa["ends_on"], wb["ends_on"]),
            "text": text_a, "conflict": True}


# --------------------------------------------------------- matching

_PAREN_RE = re.compile(r"\([^)]*\)")
_NONALNUM_RE = re.compile(r"[^a-z0-9]+")


def name_key(name: str) -> str:
    """The comparable core of a name: lowercased, parenthetical
    asides (models add translations there) removed, punctuation
    collapsed."""
    core = _PAREN_RE.sub(" ", (name or "").lower())
    return _NONALNUM_RE.sub(" ", core).strip()


def _names_match(key_a: str, key_b: str) -> bool:
    """Same item when the keys are equal or one's token set
    contains the other's. A single shared token is never enough
    on its own unless the keys are identical."""
    if not key_a or not key_b:
        return False
    if key_a == key_b:
        return True
    ta, tb = set(key_a.split()), set(key_b.split())
    return ta <= tb or tb <= ta


_UNIT_SYNONYMS = (
    (r"\blbs\b", "lb"), (r"\blitre\b|\bliter\b|\bltr\b", "l"),
    (r"\bgrams\b|\bgms\b", "gm"), (r"\bounces\b", "oz"),
)


def unit_key(unit: str | None) -> str | None:
    """The comparable form of a printed size/unit, or None."""
    if not unit:
        return None
    u = _NONALNUM_RE.sub(" ", str(unit).lower()).strip()
    for pattern, repl in _UNIT_SYNONYMS:
        u = re.sub(pattern, repl, u)
    return _NONALNUM_RE.sub(" ", u).strip() or None


def _text_values_match(a, b) -> bool:
    ka, kb = name_key(str(a)), name_key(str(b))
    if not ka or not kb:
        return str(a).strip() == str(b).strip()
    return ka == kb or _names_match(ka, kb)


def _field_values_match(kind: str, a, b) -> bool:
    if kind == "text":
        return _text_values_match(a, b)
    return a == b  # money/written cents, ISO dates, numbers:
    # normalised forms compare exactly


# ------------------------------------------------------- reconciling

def reconcile(read_a: dict | None, read_b: dict | None,
              doc_type: str,
              generic_fields: list[str] | None = None) -> dict:
    """Two independent reads of ONE image -> per-field and
    per-item verdicts:

      {"image_unreadable": bool,
       "agreed_fields": {name: normalised value},
       "agreed_items": [{name, price_cents, unit, qty, ...}],
       "refusals": [{subject, name, reason, detail, ...}]}

    Refusal precedence: ambiguous_price_format (the printed value
    cannot be resolved at all) before field_conflict /
    models_disagree (two resolved values that conflict) before
    single_read_only. image_unreadable is True when either read is
    unusable (None) — a failed read confirms nothing, so the whole
    image is refused rather than demoted to single-read evidence.
    A field/item absent from BOTH reads is not a refusal: the gate
    judges evidence, and absence from both reads is recorded in
    the counts as not_seen, not as a refusal."""
    if read_a is None or read_b is None:
        return {"image_unreadable": True, "agreed_fields": {},
                "agreed_items": [], "refusals": []}
    schema = schema_for(doc_type, generic_fields)
    kinds = schema.get("fields") or {}
    agreed_fields: dict[str, object] = {}
    refusals: list[dict] = []
    fa, fb = read_a.get("fields") or {}, read_b.get("fields") or {}
    for name, kind in kinds.items():
        ea, eb = fa.get(name), fb.get(name)
        if ea is None and eb is None:
            continue
        if ea is None or eb is None:
            present = ea or eb
            reason = ("ambiguous_price_format" if present["ambiguous"]
                      else "single_read_only")
            refusals.append({
                "subject": "field", "name": name, "reason": reason,
                "detail": "seen by one read only",
                "value_a": ea["value"] if ea else None,
                "value_b": eb["value"] if eb else None})
            continue
        if ea["ambiguous"] or eb["ambiguous"]:
            refusals.append({
                "subject": "field", "name": name,
                "reason": "ambiguous_price_format",
                "detail": "printed format is ambiguous in at "
                          "least one read",
                "value_a": ea["value"], "value_b": eb["value"]})
            continue
        if _field_values_match(kind, ea["value"], eb["value"]):
            agreed_fields[name] = ea["value"]
        else:
            refusals.append({
                "subject": "field", "name": name,
                "reason": "field_conflict",
                "detail": f"{kind}_conflict",
                "value_a": ea["value"], "value_b": eb["value"]})
    agreed_items: list[dict] = []
    if schema.get("items"):
        a_items = [(name_key(p["name"]), p)
                   for p in read_a.get("items") or []]
        b_items = [(name_key(p["name"]), p)
                   for p in read_b.get("items") or []]
        used_b: set[int] = set()
        for key_a, pa in a_items:
            best = None
            for j, (key_b, pb) in enumerate(b_items):
                if j in used_b or not _names_match(key_a, key_b):
                    continue
                overlap = len(set(key_a.split()) & set(key_b.split()))
                if best is None or overlap > best[0]:
                    best = (overlap, j, pb)
            if best is None:
                reason = ("ambiguous_price_format" if pa["ambiguous"]
                          else "single_read_only")
                refusals.append({
                    "subject": "item", "name": pa["name"],
                    "reason": reason, "detail": "seen by read A only",
                    "value_a": pa["price_cents"], "value_b": None})
                continue
            _, j, pb = best
            used_b.add(j)
            if pa["ambiguous"] or pb["ambiguous"]:
                refusals.append({
                    "subject": "item", "name": pa["name"],
                    "reason": "ambiguous_price_format",
                    "detail": "printed price format is ambiguous "
                              "in at least one read",
                    "value_a": pa["price_cents"],
                    "value_b": pb["price_cents"]})
                continue
            if pa["price_cents"] != pb["price_cents"]:
                refusals.append({
                    "subject": "item", "name": pa["name"],
                    "reason": "models_disagree",
                    "detail": "price_conflict",
                    "value_a": pa["price_cents"],
                    "value_b": pb["price_cents"]})
                continue
            ua, ub = unit_key(pa.get("unit")), unit_key(pb.get("unit"))
            if ua and ub and ua != ub:
                refusals.append({
                    "subject": "item", "name": pa["name"],
                    "reason": "models_disagree",
                    "detail": "unit_conflict",
                    "value_a": pa["price_cents"],
                    "value_b": pb["price_cents"]})
                continue
            if (pa.get("qty") is not None and pb.get("qty") is not None
                    and pa["qty"] != pb["qty"]):
                refusals.append({
                    "subject": "item", "name": pa["name"],
                    "reason": "models_disagree",
                    "detail": "qty_conflict",
                    "value_a": pa["price_cents"],
                    "value_b": pb["price_cents"]})
                continue
            agreed_items.append({
                "name": pa["name"], "price_cents": pa["price_cents"],
                "price": pa["price_cents"] / 100.0,
                "unit": pa.get("unit") or pb.get("unit"),
                "qty": pa.get("qty") if pa.get("qty") is not None
                       else pb.get("qty"),
                "name_a": pa["name"], "name_b": pb["name"]})
        for j, (_, pb) in enumerate(b_items):
            if j not in used_b:
                reason = ("ambiguous_price_format" if pb["ambiguous"]
                          else "single_read_only")
                refusals.append({
                    "subject": "item", "name": pb["name"],
                    "reason": reason, "detail": "seen by read B only",
                    "value_a": None, "value_b": pb["price_cents"]})
    return {"image_unreadable": False,
            "agreed_fields": agreed_fields,
            "agreed_items": agreed_items, "refusals": refusals}
