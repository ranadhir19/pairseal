"""pairseal command line.

  pairseal extract IMAGE [--doc-type TYPE] [--fields a,b,c]
      [--reader gemini:MODEL] [--reader gemini:MODEL2]
      [--source URL] [--retailer NAME] [--today YYYY-MM-DD]
      [--corpus PATH] [--out PATH]
  pairseal verify-corpus PATH
  pairseal selftest [--write]

Exit codes for extract: 0 = at least one field or item sealed;
3 = the gate refused everything (a success state of the gate,
distinct from failure); 2 = usage/config error (bad image, bad
reader spec, unreadable file).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .corpus import append_record, load_records, summarise
from .gate import extract
from .providers import DEFAULT_PAIR, make_reader


def _cmd_extract(args) -> int:
    path = Path(args.image)
    if not path.is_file():
        print(f"error: no such image: {path}", file=sys.stderr)
        return 2
    specs = args.reader or list(DEFAULT_PAIR)
    try:
        readers = [make_reader(s) for s in specs]
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    today = None
    if args.today:
        try:
            today = date.fromisoformat(args.today)
        except ValueError:
            print(f"error: bad --today date: {args.today}",
                  file=sys.stderr)
            return 2
    fields = None
    if args.fields:
        fields = [f.strip() for f in args.fields.split(",")
                  if f.strip()]
    record = extract(
        path.read_bytes(), readers, doc_type=args.doc_type,
        generic_fields=fields,
        source={"url": args.source, "retailer": args.retailer,
                "posted_at": None, "note": None},
        today=today)
    text = json.dumps(record, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    if args.corpus:
        append_record(args.corpus, record)
    counts = record["counts"]
    print(f"pairseal: {record['doc_type']} — "
          f"{counts['sealed_fields']} field(s) + "
          f"{counts['sealed_items']} item(s) sealed, "
          f"{counts['refused']} refusal(s); pair distance: "
          f"{record['pair']['distance']}", file=sys.stderr)
    return 0 if record["outcome"] == "sealed" else 3


def _cmd_verify_corpus(args) -> int:
    path = Path(args.path)
    if not path.is_file():
        print(f"error: no such corpus file: {path}", file=sys.stderr)
        return 2
    records, torn = load_records(path)
    summary = summarise(records)
    summary["torn_lines_skipped"] = torn
    print(json.dumps(summary, indent=2))
    return 0 if summary["invalid_records"] == 0 else 3


def _cmd_selftest(args) -> int:
    from .selftest import render_report, run_selftest
    report = run_selftest()
    rendered = render_report(report)
    if args.write:
        target = Path(__file__).resolve().parents[2] \
            / "SELFTEST_RESULTS.md"
        target.write_text(rendered, encoding="utf-8")
        print(f"wrote {target}")
    else:
        print(rendered)
    return 0 if not report["problems"] else 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pairseal",
        description="Dual-read agreement-gated field extraction "
                    "from document images. Only fields and items "
                    "two independent vision-model reads agree on "
                    "are sealed; everything else is a reason-coded "
                    "refusal.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ex = sub.add_parser("extract", help="extract one image")
    p_ex.add_argument("image")
    p_ex.add_argument("--doc-type", default="auto",
                      choices=["auto", "flyer", "invoice", "receipt",
                               "check", "generic"])
    p_ex.add_argument("--fields", default=None,
                      help="comma-separated field names (generic "
                           "document type)")
    p_ex.add_argument("--reader", action="append", default=None,
                      help="reader spec: gemini:<model>, "
                           "groq:<model>, or ocr (repeatable; "
                           "default pair: "
                           + " + ".join(DEFAULT_PAIR) + ")")
    p_ex.add_argument("--source", default=None,
                      help="source URL recorded as provenance")
    p_ex.add_argument("--retailer", default=None)
    p_ex.add_argument("--today", default=None,
                      help="evaluation date YYYY-MM-DD (window "
                           "gate; default: today)")
    p_ex.add_argument("--corpus", default=None,
                      help="append the record to this JSONL corpus")
    p_ex.add_argument("--out", default=None,
                      help="write the JSON record here instead of "
                           "stdout")
    p_ex.set_defaults(func=_cmd_extract)

    p_vc = sub.add_parser("verify-corpus",
                          help="validate + summarise a corpus log")
    p_vc.add_argument("path")
    p_vc.set_defaults(func=_cmd_verify_corpus)

    p_st = sub.add_parser("selftest",
                          help="run the fault-injection self-test")
    p_st.add_argument("--write", action="store_true",
                      help="write SELFTEST_RESULTS.md at the repo "
                           "root")
    p_st.set_defaults(func=_cmd_selftest)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
