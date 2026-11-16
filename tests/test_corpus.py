import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from pairseal.corpus import append_record, load_records, summarise
from pairseal.gate import extract
from pairseal.selftest import ScriptedReader, _item, _read

TODAY = date(2026, 12, 2)
PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"


def _record():
    items = [_item("Acme Rice", 4.99, "$4.99", "4 lb")]
    return extract(PNG, [ScriptedReader("a", _read("flyer",
                                                   items=items)),
                         ScriptedReader("b", _read("flyer",
                                                   items=list(items)))],
                   doc_type="flyer", today=TODAY)


class CorpusTests(unittest.TestCase):
    def test_append_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "corpus.jsonl"
            append_record(path, _record())
            append_record(path, _record())
            records, torn = load_records(path)
            self.assertEqual(len(records), 2)
            self.assertEqual(torn, 0)

    def test_torn_final_line_tolerated(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "corpus.jsonl"
            append_record(path, _record())
            with open(path, "a", encoding="utf-8") as fh:
                fh.write('{"schema_version": "1.1", "run_id": "x')
            records, torn = load_records(path)
            self.assertEqual(len(records), 1)
            self.assertEqual(torn, 1)

    def test_summarise(self):
        records = [_record(), _record()]
        summary = summarise(records)
        self.assertEqual(summary["records"], 2)
        self.assertEqual(summary["sealed"], 2)
        self.assertEqual(summary["sealed_items"], 2)
        self.assertEqual(summary["invalid_records"], 0)
        self.assertEqual(summary["doc_types"], {"flyer": 2})
        self.assertEqual(summary["pair_distances"],
                         {"same_family": 2})

    def test_record_is_json_serialisable(self):
        json.dumps(_record())


if __name__ == "__main__":
    unittest.main()
