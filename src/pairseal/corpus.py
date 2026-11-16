"""The corpus log: append-only JSONL of extraction records.

One JSON object per line, schema 1.1 (pairseal.schema). The log
is the deployment's audit trail: what was sealed, what was
refused, by which model pair, on which image (by hash). Appends
are single writes of one line; readers tolerate a torn final line
(a crashed append) by skipping it with a warning count.
"""

from __future__ import annotations

import json
from pathlib import Path

from .schema import validate_record


def append_record(path: str | Path, record: dict) -> None:
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def load_records(path: str | Path) -> tuple[list[dict], int]:
    """(records, torn_lines_skipped)."""
    records: list[dict] = []
    torn = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                torn += 1
    return records, torn


def summarise(records: list[dict]) -> dict:
    """Corpus-level counts: outcomes, refusal reasons, doc types,
    pair distances, and per-record validation problems."""
    out = {"records": len(records), "sealed": 0, "refused": 0,
           "sealed_fields": 0, "sealed_items": 0,
           "refusal_reasons": {}, "doc_types": {},
           "pair_distances": {}, "invalid_records": 0}
    for rec in records:
        if validate_record(rec):
            out["invalid_records"] += 1
        if rec.get("outcome") == "sealed":
            out["sealed"] += 1
        else:
            out["refused"] += 1
        counts = rec.get("counts") or {}
        out["sealed_fields"] += counts.get("sealed_fields", 0)
        out["sealed_items"] += counts.get("sealed_items", 0)
        for r in rec.get("refusals") or []:
            reason = r.get("reason")
            out["refusal_reasons"][reason] = \
                out["refusal_reasons"].get(reason, 0) + 1
        dt = rec.get("doc_type")
        out["doc_types"][dt] = out["doc_types"].get(dt, 0) + 1
        dist = (rec.get("pair") or {}).get("distance")
        out["pair_distances"][dist] = \
            out["pair_distances"].get(dist, 0) + 1
    return out
