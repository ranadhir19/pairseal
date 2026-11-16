import json
import tempfile
import unittest
from pathlib import Path

from pairseal.cli import main
from pairseal.corpus import append_record
from pairseal.gate import extract
from pairseal.selftest import ScriptedReader, _item, _read
from datetime import date

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"


class CliTests(unittest.TestCase):
    def test_missing_image_is_usage_error(self):
        self.assertEqual(main(["extract", "/nonexistent/x.png"]), 2)

    def test_bad_reader_spec_is_usage_error(self):
        with tempfile.TemporaryDirectory() as td:
            img = Path(td) / "x.png"
            img.write_bytes(PNG)
            self.assertEqual(
                main(["extract", str(img), "--reader", "bogus"]), 2)

    def test_verify_corpus(self):
        items = [_item("Acme Rice", 4.99, "$4.99", "4 lb")]
        rec = extract(PNG, [ScriptedReader("a", _read("flyer",
                                                      items=items)),
                            ScriptedReader("b", _read(
                                "flyer", items=list(items)))],
                      doc_type="flyer", today=date(2026, 12, 2))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "c.jsonl"
            append_record(path, rec)
            self.assertEqual(main(["verify-corpus", str(path)]), 0)

    def test_verify_corpus_missing_file(self):
        self.assertEqual(main(["verify-corpus", "/nope.jsonl"]), 2)


if __name__ == "__main__":
    unittest.main()
