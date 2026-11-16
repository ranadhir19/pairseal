import json
import unittest
from datetime import date

from pairseal.reads import (merge_windows, name_key, parse_read,
                             parse_window, price_text_to_cents,
                             reconcile, resolve_price, unit_key,
                             written_amount_to_cents)


class PriceTextTests(unittest.TestCase):
    def test_plain_decimal(self):
        self.assertEqual(price_text_to_cents("5.99"), (599, False))
        self.assertEqual(price_text_to_cents("$5.99"), (599, False))
        self.assertEqual(price_text_to_cents("12.00"), (1200, False))

    def test_one_decimal_place_is_tenths(self):
        self.assertEqual(price_text_to_cents("5.9"), (590, False))

    def test_flyer_spaced_cents(self):
        self.assertEqual(price_text_to_cents("$5 99"), (599, False))

    def test_slash_cents(self):
        self.assertEqual(price_text_to_cents("5/99"), (599, False))

    def test_bare_integer_three_digits_is_ambiguous(self):
        self.assertEqual(price_text_to_cents("599"), (None, True))

    def test_garbled_groups_are_ambiguous(self):
        self.assertEqual(price_text_to_cents("599 99 99"),
                         (None, True))

    def test_bare_small_integer_is_whole_dollars(self):
        self.assertEqual(price_text_to_cents("5"), (500, False))
        self.assertEqual(price_text_to_cents("12"), (1200, False))

    def test_empty(self):
        self.assertEqual(price_text_to_cents(""), (None, False))
        self.assertEqual(price_text_to_cents(None), (None, False))


class ResolvePriceTests(unittest.TestCase):
    def test_number_only(self):
        self.assertEqual(resolve_price(5.99, None), (599, False))

    def test_text_governs_when_consistent(self):
        self.assertEqual(resolve_price(5.99, "$5.99"), (599, False))

    def test_number_contradicting_text_is_ambiguous(self):
        self.assertEqual(resolve_price(599.0, "599"), (None, True))

    def test_nothing(self):
        self.assertEqual(resolve_price(None, None), (None, False))


class WrittenAmountTests(unittest.TestCase):
    def test_standard_legal_line(self):
        self.assertEqual(
            written_amount_to_cents(
                "Two hundred fifty and 00/100 dollars"), 25000)

    def test_thousands_with_cents(self):
        self.assertEqual(
            written_amount_to_cents(
                "One thousand two hundred thirty-four and 56/100"),
            123456)

    def test_hyphenated(self):
        self.assertEqual(
            written_amount_to_cents("Seventy-five and 25/100"),
            7525)

    def test_cents_only(self):
        self.assertEqual(written_amount_to_cents("Fifty cents"), 50)

    def test_unresolvable(self):
        self.assertIsNone(written_amount_to_cents("a lot of money"))
        self.assertIsNone(written_amount_to_cents(None))
        self.assertIsNone(written_amount_to_cents(""))


class WindowTests(unittest.TestCase):
    def test_numeric_range(self):
        w = parse_window("Sale valid 09/18/2026 to 09/20/2026")
        self.assertEqual(w["starts_on"], date(2026, 9, 18))
        self.assertEqual(w["ends_on"], date(2026, 9, 20))

    def test_two_digit_year(self):
        w = parse_window("valid Sept-18 to Sept-20-26")
        self.assertEqual(w["ends_on"], date(2026, 9, 20))

    def test_month_names(self):
        w = parse_window("Dec 1, 2026 - Dec 3, 2026")
        self.assertEqual(w["starts_on"], date(2026, 12, 1))
        self.assertEqual(w["ends_on"], date(2026, 12, 3))

    def test_no_dates(self):
        self.assertEqual(parse_window("on sale now!"),
                         {"starts_on": None, "ends_on": None})
        self.assertEqual(parse_window(None),
                         {"starts_on": None, "ends_on": None})

    def test_merge_conflict_narrows(self):
        w = merge_windows("12/01/2026 to 12/05/2026",
                          "12/02/2026 to 12/03/2026")
        self.assertTrue(w["conflict"])
        self.assertEqual(w["starts_on"], date(2026, 12, 2))
        self.assertEqual(w["ends_on"], date(2026, 12, 3))


class ParseReadTests(unittest.TestCase):
    def test_flyer_read(self):
        raw = json.dumps({
            "doc_type": "flyer", "unreadable": False,
            "validity_text": "Sale valid 12/01/2026 to 12/03/2026",
            "fields": {},
            "items": [{"name": "Acme Rice", "value": 4.99,
                       "value_text": "$4.99", "unit": "4 lb",
                       "qty": None}]})
        read = parse_read(raw, "flyer")
        self.assertFalse(read["unreadable"])
        self.assertEqual(read["items"][0]["price_cents"], 499)

    def test_malformed_is_none(self):
        self.assertIsNone(parse_read("not json", "flyer"))
        self.assertIsNone(parse_read(None, "flyer"))
        self.assertIsNone(parse_read("[1, 2]", "flyer"))

    def test_abstaining_read(self):
        raw = json.dumps({"doc_type": "flyer", "unreadable": True,
                          "fields": {}, "items": []})
        read = parse_read(raw, "flyer")
        self.assertTrue(read["unreadable"])
        self.assertEqual(read["items"], [])

    def test_check_read_normalises_amounts(self):
        raw = json.dumps({
            "doc_type": "check", "unreadable": False,
            "validity_text": None,
            "fields": {
                "amount_numeric": {"value": 250.0,
                                   "value_text": "$250.00"},
                "amount_written": {
                    "value": "Two hundred fifty and 00/100 dollars",
                    "value_text": "Two hundred fifty and 00/100"}},
            "items": []})
        read = parse_read(raw, "check")
        self.assertEqual(read["fields"]["amount_numeric"]["value"],
                         25000)
        self.assertEqual(read["fields"]["amount_written"]["value"],
                         25000)

    def test_placeholder_price_dropped(self):
        raw = json.dumps({
            "doc_type": "flyer", "unreadable": False,
            "validity_text": None, "fields": {},
            "items": [{"name": "Message Me Special", "value": 0,
                       "value_text": "$0", "unit": None,
                       "qty": None}]})
        read = parse_read(raw, "flyer")
        self.assertEqual(read["items"], [])


class MatchHelperTests(unittest.TestCase):
    def test_name_key(self):
        self.assertEqual(name_key("Acme Toor Dal (Pigeon Peas)"),
                         "acme toor dal")

    def test_unit_key_synonyms(self):
        self.assertEqual(unit_key("4 lbs"), "4 lb")
        self.assertEqual(unit_key("1 Litre"), "1 l")
        self.assertIsNone(unit_key(None))


class ReconcileTests(unittest.TestCase):
    def _flyer(self, items):
        return {"doc_type": "flyer", "unreadable": False,
                "validity_text": None, "fields": {},
                "items": items}

    def _item(self, name, cents, unit=None, ambiguous=False):
        return {"name": name, "price_cents": cents,
                "ambiguous": ambiguous, "unit": unit, "qty": None}

    def test_agreement(self):
        v = reconcile(self._flyer([self._item("Rice", 499)]),
                      self._flyer([self._item("Rice", 499)]),
                      "flyer")
        self.assertEqual(len(v["agreed_items"]), 1)
        self.assertEqual(v["refusals"], [])

    def test_price_conflict(self):
        v = reconcile(self._flyer([self._item("Rice", 499)]),
                      self._flyer([self._item("Rice", 449)]),
                      "flyer")
        self.assertEqual(v["refusals"][0]["reason"],
                         "models_disagree")

    def test_failed_read_refuses_image(self):
        v = reconcile(None, self._flyer([self._item("Rice", 499)]),
                      "flyer")
        self.assertTrue(v["image_unreadable"])


if __name__ == "__main__":
    unittest.main()
