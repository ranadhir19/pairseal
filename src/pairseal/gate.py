"""The agreement gate: two independent reads in, sealed fields and
items (or reason-coded refusals) out.

Pipeline per image:
  1. hash the image (the corpus stores the hash, never the image);
  2. run each reader of the configured pair (a provider error is
     recorded per reader; a 429 stops that provider for the run —
     it is never retried in a loop);
  3. parse each raw reply against the document type's schema;
  4. reconcile the two parsed reads deterministically;
  5. apply the document-level gates: the flyer validity-window
     expiry gate, and the check amount cross-check (numeric amount
     must equal the written amount — a check whose two amount
     forms disagree is refused even when both models agree with
     each other, because the document contradicts itself);
  6. build the corpus record, applying the privacy rule for
     sensitive fields (names + outcomes + hashes only).

Precision-first throughout: a refusal is a success state of the
gate, not an error. The function's result IS the corpus record
(schema 1.1, pairseal.schema); callers persist it via
pairseal.corpus.
"""

from __future__ import annotations

import hashlib
from datetime import date

from . import __version__
from .doctype import is_sensitive, schema_for
from .providers import ProviderError
from .reads import merge_windows, parse_read, reconcile
from .schema import (SCHEMA_VERSION, TOOL_NAME, new_run_id,
                     pair_block, utc_now_iso)

_MEDIA_TYPES = {
    b"\x89PNG": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"GIF8": "image/gif",
    b"RIFF": "image/webp",
}


def sniff_media_type(data: bytes) -> str:
    for magic, mime in _MEDIA_TYPES.items():
        if data.startswith(magic):
            return mime
    return "application/octet-stream"


def _run_readers(image_bytes: bytes, media_type: str,
                 readers: list, doc_type: str,
                 generic_fields: list[str] | None) -> list[dict]:
    """Run the readers; return per-reader outcome dicts. A reader
    that raises ProviderError is recorded as an error read — the
    run continues with the other reader, whose evidence then
    stands as single-read (and is refused as such)."""
    outcomes = []
    for reader in readers:
        entry = {"reader": None, "status": "error", "error": None,
                 "raw_text": None, "parsed": None, "usage": None,
                 "_reader_obj": reader}
        try:
            result = reader.read(image_bytes, media_type,
                                 doc_type=doc_type,
                                 generic_fields=generic_fields)
            entry["raw_text"] = result.get("raw_text")
            entry["usage"] = result.get("usage")
            parsed = parse_read(result.get("raw_text"), doc_type,
                                generic_fields)
            entry["parsed"] = parsed
            if parsed is None:
                entry["status"] = "unreadable"
            elif parsed.get("unreadable"):
                entry["status"] = "unreadable"
            else:
                entry["status"] = "ok"
        except ProviderError as e:
            entry["error"] = {"code": e.code, "status": e.status}
        except Exception as e:  # noqa: BLE001 — a broken reader is
            # an error read, never a crashed gate
            entry["error"] = {"code": "reader_error", "status": None,
                              "message": repr(e)[:200]}
        entry["reader"] = reader.identity()
        outcomes.append(entry)
    return outcomes


def _resolve_doc_type(requested: str, outcomes: list[dict]) -> tuple[str, str]:
    """(effective doc type, source). User-specified wins. Under
    auto: the type both usable reads report; if they conflict, the
    generic schema (a documented fallback — the gate still judges
    every field it can, and the record says what happened)."""
    if requested != "auto":
        return requested, "user"
    seen = [o["parsed"]["doc_type"] for o in outcomes
            if o["parsed"] and not o["parsed"].get("unreadable")]
    if not seen:
        return "generic", "auto_fallback_generic"
    if len(set(seen)) == 1:
        return seen[0], "auto"
    return "generic", "auto_fallback_generic"


def extract(image_bytes: bytes, readers: list,
            doc_type: str = "auto",
            generic_fields: list[str] | None = None,
            source: dict | None = None,
            today: date | None = None) -> dict:
    """Run the gate on one image. `readers` are provider objects
    (pairseal.providers); the gate reconciles the FIRST TWO usable
    reads and records any further readers as additional evidence.
    Returns the corpus record (schema 1.1)."""
    today = today or date.today()
    media_type = sniff_media_type(image_bytes)
    digest = hashlib.sha256(image_bytes).hexdigest()
    outcomes = _run_readers(image_bytes, media_type, readers,
                            doc_type, generic_fields)
    effective_type, type_source = _resolve_doc_type(doc_type,
                                                    outcomes)
    if doc_type == "auto" and effective_type != "generic":
        # Re-parse under the detected type so its schema governs.
        for o in outcomes:
            if o["raw_text"] is not None and o["error"] is None:
                o["parsed"] = parse_read(o["raw_text"],
                                         effective_type,
                                         generic_fields)
                if o["parsed"] is None or o["parsed"].get("unreadable"):
                    o["status"] = "unreadable"
                else:
                    o["status"] = "ok"
    schema = schema_for(effective_type, generic_fields)
    usable = [o for o in outcomes if o["parsed"] is not None]
    errors = [o for o in outcomes if o["error"] is not None]

    pair = pair_block([o["reader"] for o in outcomes[:2]])
    record = {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": TOOL_NAME, "version": __version__},
        "run_id": new_run_id(),
        "recorded_at": utc_now_iso(),
        "doc_type": effective_type,
        "doc_type_source": type_source,
        "image": {"sha256": digest, "bytes": len(image_bytes),
                  "media_type": media_type},
        "source": source or {"url": None, "retailer": None,
                             "posted_at": None, "note": None},
        "evaluation_date": today.isoformat(),
        "pair": pair,
        "reads": [],
        "window": None,
        "fields": [],
        "tuples": [],
        "refusals": [],
        "outcome": "refused",
        "counts": {"sealed_fields": 0, "sealed_items": 0,
                   "refused": 0},
    }

    def refuse(subject, name, reason, detail, value_a=None,
               value_b=None):
        sensitive = (subject == "field"
                     and is_sensitive(effective_type, name or ""))
        entry = {"subject": subject, "name": name,
                 "reason": reason, "detail": detail,
                 "redacted": bool(sensitive)}
        if not sensitive:
            entry["value_a"] = value_a
            entry["value_b"] = value_b
        record["refusals"].append(entry)

    # ---- image-level outcomes ------------------------------------
    if len(usable) < 2:
        if errors and len(usable) == 0:
            codes = ", ".join(
                sorted({o["error"]["code"] for o in errors}))
            refuse("image", None, "provider_error",
                   f"no usable read; provider errors: {codes}")
        elif len(usable) == 0:
            refuse("image", None, "unreadable",
                   "no read could read this image")
        # With exactly one usable read, fall through: reconcile it
        # against an empty read so every field/item it carries is
        # refused single_read_only (or ambiguous), with the failed
        # read's error kept in the reads block.
    if len(usable) >= 1:
        read_a = usable[0]["parsed"]
        read_b = (usable[1]["parsed"] if len(usable) >= 2 else
                  {"doc_type": effective_type, "unreadable": False,
                   "validity_text": None, "fields": {}, "items": []})
        verdict = reconcile(read_a, read_b, effective_type,
                            generic_fields)
        if verdict["image_unreadable"]:
            refuse("image", None, "unreadable",
                   "a read could not read this image")
        else:
            # ---- window gate (flyer) ----
            expired = False
            if schema.get("window"):
                window = merge_windows(
                    read_a.get("validity_text"),
                    read_b.get("validity_text"))
                ends = window["ends_on"]
                expired = bool(ends and ends < today)
                record["window"] = {
                    "validity_text": window["text"],
                    "starts_on": (window["starts_on"].isoformat()
                                  if window["starts_on"] else None),
                    "ends_on": (ends.isoformat() if ends else None),
                    "expired": expired,
                    "conflict": window["conflict"],
                }
            # ---- field verdicts ----
            for name, value in verdict["agreed_fields"].items():
                if expired:
                    refuse("field", name, "expired_window",
                           "agreed value inside an expired "
                           "validity window", value_a=value)
                    continue
                sensitive = is_sensitive(effective_type, name)
                entry = {"name": name, "redacted": bool(sensitive)}
                if not sensitive:
                    entry["value"] = value
                record["fields"].append(entry)
            for r in verdict["refusals"]:
                if r["subject"] == "field":
                    refuse("field", r["name"], r["reason"],
                           r["detail"], r.get("value_a"),
                           r.get("value_b"))
            # ---- check cross-check ----
            if "amount_words_match" in (schema.get("cross_checks")
                                        or ()):
                fields_now = {f["name"]: f for f in record["fields"]}
                num = verdict["agreed_fields"].get("amount_numeric")
                words = verdict["agreed_fields"].get("amount_written")
                if num is not None and words is not None \
                        and num != words:
                    for fname in ("amount_numeric", "amount_written"):
                        if fname in fields_now:
                            record["fields"].remove(fields_now[fname])
                    refuse("field", "amount_numeric",
                           "cross_field_mismatch",
                           "numeric amount and written amount "
                           "disagree inside the document")
                    refuse("field", "amount_written",
                           "cross_field_mismatch",
                           "numeric amount and written amount "
                           "disagree inside the document")
            # ---- item verdicts ----
            for item in verdict["agreed_items"]:
                if expired:
                    refuse("item", item["name"], "expired_window",
                           "agreed price inside an expired "
                           "validity window",
                           value_a=item["price_cents"])
                    continue
                record["tuples"].append(item)
            for r in verdict["refusals"]:
                if r["subject"] == "item":
                    refuse("item", r["name"], r["reason"],
                           r["detail"], r.get("value_a"),
                           r.get("value_b"))
            if (not verdict["agreed_fields"]
                    and not verdict["agreed_items"]
                    and not verdict["refusals"]
                    and not record["refusals"]):
                if all(o["parsed"].get("unreadable")
                       for o in usable):
                    refuse("image", None, "unreadable",
                           "every usable read abstained as "
                           "unreadable")
                else:
                    refuse("image", None, "no_price_found",
                           "reads were usable but nothing "
                           "extractable was found")

    # ---- reads block (privacy-redacted where required) -----------
    any_sensitive = bool(schema.get("sensitive_fields")) or any(
        is_sensitive(effective_type, n)
        for n in (schema.get("fields") or {}))
    for o in outcomes:
        parsed = o["parsed"]
        if any_sensitive and parsed is not None:
            parsed = _redact_parsed(parsed, effective_type)
        record["reads"].append({
            "reader": o["reader"], "status": o["status"],
            "error": o["error"],
            "raw_text": (None if any_sensitive else o["raw_text"]),
            "raw_redacted": bool(any_sensitive),
            "parsed": parsed, "usage": o["usage"],
        })

    record["counts"] = {
        "sealed_fields": len(record["fields"]),
        "sealed_items": len(record["tuples"]),
        "refused": len(record["refusals"]),
    }
    if record["fields"] or record["tuples"]:
        record["outcome"] = "sealed"
    return record


def _redact_parsed(parsed: dict, doc_type: str) -> dict:
    """A parsed read with sensitive field values removed (the
    field's presence and normalised shape stay visible as
    agreement evidence; the value does not)."""
    out = {**parsed, "fields": {}}
    for name, entry in (parsed.get("fields") or {}).items():
        if is_sensitive(doc_type, name):
            out["fields"][name] = {"value": None, "raw": None,
                                   "ambiguous": entry["ambiguous"],
                                   "redacted": True}
        else:
            out["fields"][name] = entry
    return out
