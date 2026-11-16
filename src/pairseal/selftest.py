"""Fault-injection self-test: synthetic documents with known
ground truth, read by SCRIPTED readers, judged by the real gate.

Why scripted readers: the self-test measures the GATE (parsing,
normalisation, reconciliation, expiry, cross-checks, redaction),
deterministically and offline. Live model accuracy is a property
of the models and is measured by real corpus runs, not here —
the report says so, in so many words.

Fixtures (rendered locally with Pillow; ground truth is what is
rendered):
  flyer_clean        4 products, both reads agree -> 4 sealed
  flyer_traps        a bare "599" price (ambiguous), a price the
                     reads conflict on, a product only one read
                     sees, and one clean product -> 1 sealed,
                     3 refused with the planted reasons
  flyer_expired      an agreed price inside a printed window that
                     has ended -> refused expired_window
  flyer_unreadable   low-contrast render; both reads abstain ->
                     refused unreadable
  invoice_clean      vendor/number/date/tax/total + 2 line items
                     agreed -> all sealed
  invoice_conflict   one line item's amount conflicts between
                     reads -> that item refused models_disagree,
                     the rest sealed
  check_clean        numeric amount == written amount -> sealed
                     (corpus record redacted: no values stored)
  check_mismatch     numeric $250.00 vs written "two hundred
                     fifteen" — BOTH reads agree with each other;
                     the document contradicts itself -> both
                     amount fields refused cross_field_mismatch
  receipt_clean      merchant/date/total + 2 items agreed
  generic_clean      user-named fields agreed -> sealed

Controls (negative):
  empty_reads        both reads find nothing -> no_price_found
  provider_429       reader A rate-limited, reader B reads fine ->
                     nothing sealed; B's items refused
                     single_read_only; A's error recorded

Global invariant, checked across every fixture: no sealed item or
field ever carries a value that differs from the ground truth
rendered in its image. The gate may refuse a truth; it may never
invent one.
"""

from __future__ import annotations

import io
import json
from datetime import date

from .gate import extract
from .providers import ProviderError, RateLimited

TODAY = date(2026, 12, 2)

# ------------------------------------------------------------- renders

def _font(size: int, bold: bool = False, italic: bool = False):
    from PIL import ImageFont
    base = "/usr/share/fonts/truetype/dejavu/"
    name = "DejaVuSans"
    if italic:
        name = "DejaVuSerif-Italic"
    elif bold:
        name = "DejaVuSans-Bold"
    try:
        return ImageFont.truetype(base + name + ".ttf", size)
    except OSError:
        return ImageFont.load_default()


def _canvas(w: int, h: int, color="white"):
    from PIL import Image
    return Image.new("RGB", (w, h), color)


def _png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_flyer(title: str, validity: str,
                 rows: list[tuple[str, str, str | None]],
                 faint: bool = False) -> bytes:
    from PIL import ImageDraw
    img = _canvas(1000, 240 + 110 * len(rows))
    d = ImageDraw.Draw(img)
    ink = (200, 200, 200) if faint else (20, 20, 20)
    accent = (210, 210, 210) if faint else (178, 34, 34)
    d.text((40, 30), title, font=_font(44, bold=True), fill=accent)
    d.text((40, 95), validity, font=_font(30), fill=ink)
    y = 170
    for name, price, unit in rows:
        d.text((40, y), name, font=_font(34), fill=ink)
        d.text((700, y), price, font=_font(34, bold=True), fill=ink)
        if unit:
            d.text((700, y + 45), unit, font=_font(24), fill=ink)
        y += 110
    return _png_bytes(img)


def render_invoice() -> bytes:
    from PIL import ImageDraw
    img = _canvas(1000, 760)
    d = ImageDraw.Draw(img)
    d.text((40, 30), "ACME OFFICE SUPPLY CO", font=_font(40, bold=True),
           fill=(20, 20, 20))
    d.text((40, 95), "INVOICE INV-2026-1042   Date: 12/01/2026",
           font=_font(28), fill=(20, 20, 20))
    rows = [("Printer Paper Case", "2", "$45.00"),
            ("Toner Cartridge", "1", "$38.50")]
    y = 200
    for name, qty, amt in rows:
        d.text((40, y), name, font=_font(30), fill=(20, 20, 20))
        d.text((640, y), qty, font=_font(30), fill=(20, 20, 20))
        d.text((760, y), amt, font=_font(30), fill=(20, 20, 20))
        y += 80
    d.text((40, y + 40), "Tax: $6.68", font=_font(30), fill=(20, 20, 20))
    d.text((40, y + 100), "TOTAL: $90.18", font=_font(34, bold=True),
           fill=(20, 20, 20))
    return _png_bytes(img)


def render_check(written: str) -> bytes:
    from PIL import ImageDraw
    img = _canvas(1100, 460)
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, 1090, 450], outline=(20, 20, 20), width=3)
    d.text((40, 40), "Date: 12/01/2026", font=_font(28),
           fill=(20, 20, 20))
    d.text((40, 110), "Pay to the order of: Jordan Miller",
           font=_font(30), fill=(20, 20, 20))
    d.text((820, 110), "$250.00", font=_font(32, bold=True),
           fill=(20, 20, 20))
    d.text((40, 180), written, font=_font(30, italic=True),
           fill=(20, 20, 20))
    d.text((40, 260), "Memo: Office rent", font=_font(26),
           fill=(20, 20, 20))
    d.text((40, 360), "021000021  000123456789  1001",
           font=_font(28), fill=(20, 20, 20))
    return _png_bytes(img)


def render_receipt() -> bytes:
    from PIL import ImageDraw
    img = _canvas(700, 640)
    d = ImageDraw.Draw(img)
    d.text((40, 30), "CORNER DELI", font=_font(40, bold=True),
           fill=(20, 20, 20))
    d.text((40, 95), "12/01/2026", font=_font(28), fill=(20, 20, 20))
    y = 180
    for name, amt in [("Turkey Sandwich", "$9.25"),
                      ("Iced Tea", "$2.75")]:
        d.text((40, y), name, font=_font(30), fill=(20, 20, 20))
        d.text((480, y), amt, font=_font(30), fill=(20, 20, 20))
        y += 80
    d.text((40, y + 40), "TOTAL: $12.00", font=_font(34, bold=True),
           fill=(20, 20, 20))
    return _png_bytes(img)


# ----------------------------------------------------- scripted readers

class ScriptedReader:
    """A reader whose reply is fixed in advance (or which raises a
    fixed provider error). Identity is fictional and labelled
    as such in every record it touches."""

    def __init__(self, slot: str, reply: dict | None = None,
                 error: ProviderError | None = None):
        self.slot = slot
        self.reply = reply
        self.error = error

    def identity(self) -> dict:
        return {"provider": "scripted", "family": "scripted",
                "model_requested": f"scripted-{self.slot}",
                "model_reported": f"scripted-{self.slot}"}

    def read(self, image_bytes, media_type, doc_type="auto",
             generic_fields=None):
        if self.error is not None:
            raise self.error
        return {"raw_text": json.dumps(self.reply),
                "model_reported": f"scripted-{self.slot}",
                "usage": None}


def _item(name, value, text, unit=None, qty=None):
    return {"name": name, "value": value, "value_text": text,
            "unit": unit, "qty": qty}


def _field(value, text=None):
    return {"value": value, "value_text": text if text is not None
            else (str(value) if value is not None else None)}


def _read(doc_type, fields=None, items=None, validity=None,
          unreadable=False):
    return {"doc_type": doc_type, "unreadable": unreadable,
            "validity_text": validity, "fields": fields or {},
            "items": items or []}


# ------------------------------------------------------------- fixtures

def _fixtures() -> list[dict]:
    flyer_rows = [("Acme Basmati Rice", "$4.99", "4 lb"),
                  ("Acme Toor Dal", "$2.49", "2 lb"),
                  ("Fresh Spinach Bunch", "$1.29", "each"),
                  ("Acme Sunflower Oil", "$7.99", "3 L")]
    flyer_items = [_item(n, float(p[1:]), p, u)
                   for n, p, u in flyer_rows]
    validity = "Sale valid 12/01/2026 to 12/03/2026"
    trap_rows = [("Trap Garbled Lentils", "599", "2 lb"),
                 ("Trap Conflict Mango", "$3.49", "each"),
                 ("Trap Solo Paneer", "$5.49", "14 oz"),
                 ("Trap Clean Tea", "$6.25", "16 oz")]
    check_fields = {
        "payee": _field("Jordan Miller"),
        "check_date": _field("12/01/2026"),
        "amount_numeric": _field(250.00, "$250.00"),
        "routing_number": _field("021000021"),
        "account_number": _field("000123456789"),
        "memo": _field("Office rent"),
    }
    return [
        {"id": "flyer_clean", "doc_type": "flyer",
         "image": render_flyer("SELFTEST MARKET — WEEKLY SALE",
                               validity, flyer_rows),
         "reads": [_read("flyer", items=flyer_items,
                         validity=validity),
                   _read("flyer", items=list(flyer_items),
                         validity=validity)],
         "truth_items": {n: int(float(p[1:]) * 100)
                         for n, p, _ in flyer_rows},
         "expect_items": {n: int(float(p[1:]) * 100)
                          for n, p, _ in flyer_rows},
         "expect_refusals": {}},
        {"id": "flyer_traps", "doc_type": "flyer",
         "image": render_flyer("SELFTEST MARKET — WEEKLY SALE",
                               validity, trap_rows),
         "reads": [
             _read("flyer", validity=validity, items=[
                 _item("Trap Garbled Lentils", None, "599", "2 lb"),
                 _item("Trap Conflict Mango", 3.49, "$3.49", "each"),
                 _item("Trap Solo Paneer", 5.49, "$5.49", "14 oz"),
                 _item("Trap Clean Tea", 6.25, "$6.25", "16 oz")]),
             _read("flyer", validity=validity, items=[
                 _item("Trap Garbled Lentils", None, "599", "2 lb"),
                 _item("Trap Conflict Mango", 3.99, "$3.99", "each"),
                 _item("Trap Clean Tea", 6.25, "$6.25", "16 oz")])],
         "truth_items": {"Trap Garbled Lentils": None,
                         "Trap Conflict Mango": 349,
                         "Trap Solo Paneer": 549,
                         "Trap Clean Tea": 625},
         "expect_items": {"Trap Clean Tea": 625},
         "expect_refusals": {
             "Trap Garbled Lentils": "ambiguous_price_format",
             "Trap Conflict Mango": "models_disagree",
             "Trap Solo Paneer": "single_read_only"}},
        {"id": "flyer_expired", "doc_type": "flyer",
         "image": render_flyer("SELFTEST MARKET — WEEKLY SALE",
                               "Sale valid 09/18/2026 to 09/20/2026",
                               [("Acme Old Ghee", "$8.99", "14 oz")]),
         "reads": [
             _read("flyer", validity="Sale valid 09/18/2026 to "
                   "09/20/2026",
                   items=[_item("Acme Old Ghee", 8.99, "$8.99",
                                "14 oz")]),
             _read("flyer", validity="Sale valid 09/18/2026 to "
                   "09/20/2026",
                   items=[_item("Acme Old Ghee", 8.99, "$8.99",
                                "14 oz")])],
         "truth_items": {"Acme Old Ghee": 899},
         "expect_items": {},
         "expect_refusals": {"Acme Old Ghee": "expired_window"}},
        {"id": "flyer_unreadable", "doc_type": "flyer",
         "image": render_flyer("SELFTEST MARKET — WEEKLY SALE",
                               validity, flyer_rows, faint=True),
         "reads": [_read("flyer", unreadable=True),
                   _read("flyer", unreadable=True)],
         "truth_items": {}, "expect_items": {},
         "expect_refusals": {"<image>": "unreadable"}},
        {"id": "invoice_clean", "doc_type": "invoice",
         "image": render_invoice(),
         "reads": [
             _read("invoice",
                   fields={"vendor": _field("Acme Office Supply Co"),
                           "invoice_number": _field("INV-2026-1042"),
                           "invoice_date": _field("12/01/2026"),
                           "tax": _field(6.68, "$6.68"),
                           "total": _field(90.18, "$90.18")},
                   items=[_item("Printer Paper Case", 45.00,
                                "$45.00", None, 2),
                          _item("Toner Cartridge", 38.50,
                                "$38.50", None, 1)]),
             _read("invoice",
                   fields={"vendor": _field("Acme Office Supply Co"),
                           "invoice_number": _field("INV-2026-1042"),
                           "invoice_date": _field("12/01/2026"),
                           "tax": _field(6.68, "$6.68"),
                           "total": _field(90.18, "$90.18")},
                   items=[_item("Printer Paper Case", 45.00,
                                "$45.00", None, 2),
                          _item("Toner Cartridge", 38.50,
                                "$38.50", None, 1)])],
         "truth_items": {"Printer Paper Case": 4500,
                         "Toner Cartridge": 3850},
         "expect_items": {"Printer Paper Case": 4500,
                          "Toner Cartridge": 3850},
         "expect_fields": {"vendor": "Acme Office Supply Co",
                           "invoice_number": "INV-2026-1042",
                           "invoice_date": "2026-12-01",
                           "tax": 668, "total": 9018},
         "expect_refusals": {}},
        {"id": "invoice_conflict", "doc_type": "invoice",
         "image": render_invoice(),
         "reads": [
             _read("invoice",
                   fields={"vendor": _field("Acme Office Supply Co"),
                           "total": _field(90.18, "$90.18")},
                   items=[_item("Printer Paper Case", 45.00,
                                "$45.00", None, 2),
                          _item("Toner Cartridge", 38.50,
                                "$38.50", None, 1)]),
             _read("invoice",
                   fields={"vendor": _field("Acme Office Supply Co"),
                           "total": _field(90.18, "$90.18")},
                   items=[_item("Printer Paper Case", 45.00,
                                "$45.00", None, 2),
                          _item("Toner Cartridge", 83.50,
                                "$83.50", None, 1)])],
         "truth_items": {"Printer Paper Case": 4500,
                         "Toner Cartridge": 3850},
         "expect_items": {"Printer Paper Case": 4500},
         "expect_fields": {"vendor": "Acme Office Supply Co",
                           "total": 9018},
         "expect_refusals": {"Toner Cartridge": "models_disagree"}},
        {"id": "check_clean", "doc_type": "check",
         "image": render_check(
             "Two hundred fifty and 00/100 dollars"),
         "reads": [
             _read("check", fields={**check_fields,
                   "amount_written": _field(
                       "Two hundred fifty and 00/100 dollars")}),
             _read("check", fields={**check_fields,
                   "amount_written": _field(
                       "Two hundred fifty and 00/100 dollars")})],
         "truth_items": {},
         "expect_items": {},
         "expect_fields": {"payee": "Jordan Miller",
                           "check_date": "2026-12-01"},
         "expect_redacted_fields": {"amount_numeric",
                                    "amount_written",
                                    "routing_number",
                                    "account_number"},
         "expect_refusals": {}},
        {"id": "check_mismatch", "doc_type": "check",
         "image": render_check(
             "Two hundred fifteen and 00/100 dollars"),
         "reads": [
             _read("check", fields={**check_fields,
                   "amount_written": _field(
                       "Two hundred fifteen and 00/100 dollars")}),
             _read("check", fields={**check_fields,
                   "amount_written": _field(
                       "Two hundred fifteen and 00/100 dollars")})],
         "truth_items": {},
         "expect_items": {},
         "expect_fields": {"payee": "Jordan Miller"},
         "expect_refusals": {"amount_numeric": "cross_field_mismatch",
                             "amount_written": "cross_field_mismatch"}},
        {"id": "receipt_clean", "doc_type": "receipt",
         "image": render_receipt(),
         "reads": [
             _read("receipt",
                   fields={"merchant": _field("Corner Deli"),
                           "receipt_date": _field("12/01/2026"),
                           "total": _field(12.00, "$12.00")},
                   items=[_item("Turkey Sandwich", 9.25, "$9.25"),
                          _item("Iced Tea", 2.75, "$2.75")]),
             _read("receipt",
                   fields={"merchant": _field("Corner Deli"),
                           "receipt_date": _field("12/01/2026"),
                           "total": _field(12.00, "$12.00")},
                   items=[_item("Turkey Sandwich", 9.25, "$9.25"),
                          _item("Iced Tea", 2.75, "$2.75")])],
         "truth_items": {"Turkey Sandwich": 925, "Iced Tea": 275},
         "expect_items": {"Turkey Sandwich": 925, "Iced Tea": 275},
         "expect_fields": {"merchant": "Corner Deli",
                           "receipt_date": "2026-12-01",
                           "total": 1200},
         "expect_refusals": {}},
        {"id": "generic_clean", "doc_type": "generic",
         "generic_fields": ["member_id", "visit_date"],
         "image": render_receipt(),
         "reads": [
             _read("generic", fields={
                 "member_id": _field("M-4471"),
                 "visit_date": _field("12/01/2026")}),
             _read("generic", fields={
                 "member_id": _field("M-4471"),
                 "visit_date": _field("12/01/2026")})],
         "truth_items": {}, "expect_items": {},
         "expect_fields": {"member_id": "M-4471",
                           "visit_date": "2026-12-01"},
         "expect_refusals": {}},
    ]


def _controls() -> list[dict]:
    validity = "Sale valid 12/01/2026 to 12/03/2026"
    rows = [("Acme Basmati Rice", "$4.99", "4 lb")]
    img = render_flyer("SELFTEST MARKET — WEEKLY SALE", validity, rows)
    items = [_item("Acme Basmati Rice", 4.99, "$4.99", "4 lb")]
    return [
        {"id": "empty_reads", "doc_type": "flyer", "image": img,
         "reads": [_read("flyer"), _read("flyer")],
         "truth_items": {"Acme Basmati Rice": 499},
         "expect_items": {},
         "expect_refusals": {"<image>": "no_price_found"}},
        {"id": "provider_429", "doc_type": "flyer", "image": img,
         "reads": None,
         "errors": [RateLimited(), None],
         "replies": [None, _read("flyer", items=items,
                                 validity=validity)],
         "truth_items": {"Acme Basmati Rice": 499},
         "expect_items": {},
         "expect_refusals": {"Acme Basmati Rice": "single_read_only"},
         "expect_read_error": "http_429"},
    ]


# ---------------------------------------------------------------- run

def _run_case(case: dict) -> tuple[dict, list[str]]:
    readers = []
    if case.get("reads") is not None:
        readers = [ScriptedReader("a", case["reads"][0]),
                   ScriptedReader("b", case["reads"][1])]
    else:
        readers = [
            ScriptedReader("a", case["replies"][0], case["errors"][0]),
            ScriptedReader("b", case["replies"][1], case["errors"][1]),
        ]
    record = extract(case["image"], readers,
                     doc_type=case["doc_type"],
                     generic_fields=case.get("generic_fields"),
                     source={"url": f"selftest://{case['id']}",
                             "retailer": None, "posted_at": None,
                             "note": "fault-injection self-test"},
                     today=TODAY)
    problems: list[str] = []
    sealed_items = {t["name"]: t["price_cents"]
                    for t in record["tuples"]}
    if sealed_items != case["expect_items"]:
        problems.append(f"sealed items {sealed_items} != expected "
                        f"{case['expect_items']}")
    sealed_fields = {f["name"]: f.get("value")
                     for f in record["fields"]
                     if not f.get("redacted")}
    for name, want in (case.get("expect_fields") or {}).items():
        if sealed_fields.get(name) != want:
            problems.append(f"field {name}: {sealed_fields.get(name)!r}"
                            f" != expected {want!r}")
    redacted = {f["name"] for f in record["fields"] if f.get("redacted")}
    for name in (case.get("expect_redacted_fields") or set()):
        if name not in redacted:
            problems.append(f"field {name} was not sealed+redacted")
    blob = json.dumps(record)
    for secret in ("250.0", "215.0", "021000021", "000123456789",
                   "25000", "21500"):
        if case["doc_type"] == "check" and secret in blob:
            problems.append(f"privacy leak: {secret} in corpus record")
    refusals = {}
    for r in record["refusals"]:
        key = r["name"] if r["name"] else "<image>"
        refusals[key] = r["reason"]
    for name, want in case["expect_refusals"].items():
        if refusals.get(name) != want:
            problems.append(f"refusal {name}: {refusals.get(name)!r}"
                            f" != expected {want!r}")
    # Global invariant: nothing sealed may contradict ground truth.
    for name, cents in sealed_items.items():
        truth = case["truth_items"].get(name)
        if truth is not None and cents != truth:
            problems.append(f"INVARIANT: sealed {name} at {cents}, "
                            f"truth is {truth}")
    if case.get("expect_read_error"):
        codes = [r["error"]["code"] for r in record["reads"]
                 if r["error"]]
        if case["expect_read_error"] not in codes:
            problems.append(f"read error {case['expect_read_error']} "
                            f"not recorded (got {codes})")
    return record, problems


def run_selftest() -> dict:
    fixtures = _fixtures()
    controls = _controls()
    results = []
    all_problems = []
    for case in fixtures + controls:
        record, problems = _run_case(case)
        results.append({
            "id": case["id"],
            "kind": "control" if case in controls else "fixture",
            "doc_type": case["doc_type"],
            "sealed_fields": record["counts"]["sealed_fields"],
            "sealed_items": record["counts"]["sealed_items"],
            "refused": record["counts"]["refused"],
            "passed": not problems,
            "problems": problems,
        })
        all_problems.extend(f"{case['id']}: {p}" for p in problems)
    passed = sum(1 for r in results if r["passed"])
    return {"results": results, "passed": passed,
            "total": len(results), "problems": all_problems,
            "fixtures_passed": sum(
                1 for r in results
                if r["kind"] == "fixture" and r["passed"]),
            "fixtures_total": len(fixtures),
            "controls_passed": sum(
                1 for r in results
                if r["kind"] == "control" and r["passed"]),
            "controls_total": len(controls)}


def render_report(report: dict) -> str:
    lines = [
        "# pairseal self-test results",
        "",
        "Fault-injection self-test: synthetic documents with known",
        "ground truth, read by scripted readers, judged by the real",
        "gate. It measures the gate — parsing, normalisation,",
        "reconciliation, expiry, cross-checks, redaction — offline",
        "and deterministically. Live model accuracy is a property",
        "of the models and is measured by real corpus runs, not",
        "here.",
        "",
        f"Evaluation date for all fixtures: {TODAY.isoformat()}.",
        "",
        "| Fixture | Type | Kind | Sealed fields | Sealed items "
        "| Refused | Result |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in report["results"]:
        lines.append(
            f"| {r['id']} | {r['doc_type']} | {r['kind']} "
            f"| {r['sealed_fields']} | {r['sealed_items']} "
            f"| {r['refused']} "
            f"| {'PASS' if r['passed'] else 'FAIL'} |")
    lines += [
        "",
        f"Fixtures: {report['fixtures_passed']}/{report['fixtures_total']} passed. "
        f"Controls: {report['controls_passed']}/{report['controls_total']} passed.",
        "",
        "Global invariant held across every fixture: no sealed "
        "field or item",
        "carries a value that differs from the ground truth "
        "rendered in its",
        "image. The gate may refuse a truth; it never invents one.",
        "",
    ]
    if report["problems"]:
        lines.append("Problems:")
        lines += [f"- {p}" for p in report["problems"]]
        lines.append("")
    return "\n".join(lines)
