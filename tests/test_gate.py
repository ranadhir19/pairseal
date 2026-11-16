import json
import unittest
from datetime import date

from pairseal.gate import extract
from pairseal.providers import RateLimited
from pairseal.schema import validate_record
from pairseal.selftest import ScriptedReader, _field, _item, _read

TODAY = date(2026, 12, 2)
PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"


def pair(a, b):
    return [ScriptedReader("a", a), ScriptedReader("b", b)]


class FlyerGateTests(unittest.TestCase):
    def test_agreed_items_sealed(self):
        items = [_item("Acme Rice", 4.99, "$4.99", "4 lb")]
        rec = extract(PNG, pair(_read("flyer", items=items),
                                _read("flyer", items=list(items))),
                      doc_type="flyer", today=TODAY)
        self.assertEqual(rec["outcome"], "sealed")
        self.assertEqual(rec["tuples"][0]["price_cents"], 499)
        self.assertEqual(validate_record(rec), [])

    def test_expired_window_refuses_agreed_price(self):
        items = [_item("Acme Ghee", 8.99, "$8.99", "14 oz")]
        v = "Sale valid 09/18/2026 to 09/20/2026"
        rec = extract(PNG, pair(_read("flyer", items=items,
                                      validity=v),
                                _read("flyer", items=list(items),
                                      validity=v)),
                      doc_type="flyer", today=TODAY)
        self.assertEqual(rec["tuples"], [])
        self.assertEqual(rec["refusals"][0]["reason"],
                         "expired_window")
        self.assertTrue(rec["window"]["expired"])

    def test_single_reader_config_refuses_everything(self):
        items = [_item("Acme Rice", 4.99, "$4.99", "4 lb")]
        rec = extract(PNG, [ScriptedReader("a",
                                            _read("flyer",
                                                  items=items))],
                      doc_type="flyer", today=TODAY)
        self.assertEqual(rec["tuples"], [])
        self.assertEqual(rec["refusals"][0]["reason"],
                         "single_read_only")
        self.assertEqual(rec["pair"]["distance"], "single_reader")

    def test_rate_limited_reader_recorded_not_retried(self):
        items = [_item("Acme Rice", 4.99, "$4.99", "4 lb")]
        readers = [ScriptedReader("a", None, RateLimited()),
                   ScriptedReader("b", _read("flyer", items=items))]
        rec = extract(PNG, readers, doc_type="flyer", today=TODAY)
        self.assertEqual(rec["tuples"], [])
        codes = [r["error"]["code"] for r in rec["reads"]
                 if r["error"]]
        self.assertEqual(codes, ["http_429"])
        self.assertEqual(rec["refusals"][0]["reason"],
                         "single_read_only")

    def test_both_readers_error_is_provider_error(self):
        readers = [ScriptedReader("a", None, RateLimited()),
                   ScriptedReader("b", None, RateLimited())]
        rec = extract(PNG, readers, doc_type="flyer", today=TODAY)
        self.assertEqual(rec["refusals"][0]["reason"],
                         "provider_error")


class CheckGateTests(unittest.TestCase):
    FIELDS = {
        "payee": _field("Jordan Miller"),
        "check_date": _field("12/01/2026"),
        "amount_numeric": _field(250.00, "$250.00"),
        "routing_number": _field("021000021"),
        "account_number": _field("000123456789"),
    }

    def test_amounts_agree_and_corpus_is_redacted(self):
        fields = {**self.FIELDS, "amount_written": _field(
            "Two hundred fifty and 00/100 dollars")}
        rec = extract(PNG, pair(_read("check", fields=fields),
                                _read("check", fields=dict(fields))),
                      doc_type="check", today=TODAY)
        sealed = {f["name"]: f for f in rec["fields"]}
        self.assertIn("amount_numeric", sealed)
        self.assertTrue(sealed["amount_numeric"]["redacted"])
        self.assertNotIn("value", sealed["amount_numeric"])
        blob = json.dumps(rec)
        self.assertNotIn("021000021", blob)
        self.assertNotIn("000123456789", blob)
        self.assertNotIn("25000", blob)
        self.assertEqual(validate_record(rec), [])

    def test_amount_mismatch_caught_despite_model_agreement(self):
        fields = {**self.FIELDS, "amount_written": _field(
            "Two hundred fifteen and 00/100 dollars")}
        rec = extract(PNG, pair(_read("check", fields=fields),
                                _read("check", fields=dict(fields))),
                      doc_type="check", today=TODAY)
        sealed = {f["name"] for f in rec["fields"]}
        self.assertNotIn("amount_numeric", sealed)
        self.assertNotIn("amount_written", sealed)
        reasons = {(r["name"], r["reason"]) for r in rec["refusals"]}
        self.assertIn(("amount_numeric", "cross_field_mismatch"),
                      reasons)
        self.assertIn(("amount_written", "cross_field_mismatch"),
                      reasons)


class InvoiceGateTests(unittest.TestCase):
    def test_field_conflict(self):
        fa = {"vendor": _field("Acme"), "total": _field(90.18,
                                                        "$90.18")}
        fb = {"vendor": _field("Acme"), "total": _field(95.18,
                                                        "$95.18")}
        rec = extract(PNG, pair(_read("invoice", fields=fa),
                                _read("invoice", fields=fb)),
                      doc_type="invoice", today=TODAY)
        reasons = {(r["name"], r["reason"]) for r in rec["refusals"]}
        self.assertIn(("total", "field_conflict"), reasons)
        sealed = {f["name"] for f in rec["fields"]}
        self.assertEqual(sealed, {"vendor"})


class AutoDetectTests(unittest.TestCase):
    def test_auto_detects_receipt(self):
        fields = {"merchant": _field("Corner Deli"),
                  "total": _field(12.00, "$12.00")}
        rec = extract(PNG, pair(_read("receipt", fields=fields),
                                _read("receipt",
                                      fields=dict(fields))),
                      doc_type="auto", today=TODAY)
        self.assertEqual(rec["doc_type"], "receipt")
        self.assertEqual(rec["doc_type_source"], "auto")
        self.assertEqual(rec["outcome"], "sealed")


if __name__ == "__main__":
    unittest.main()
