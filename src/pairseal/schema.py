"""The pairseal corpus record schema (schema version 1.1).

Every extraction run produces ONE record in this shape. Records are
appended to a JSONL corpus log, so a deployment accumulates an
auditable history of what the gate sealed, what it refused, and
why — per document, per field, per model pair.

Design rules baked into the schema:

* The image itself is never stored in the corpus — only its SHA-256,
  byte count, and media type. The image stays with its source.
* Reads are stored RAW (the model's unparsed reply) alongside their
  parsed form, so the corpus can be re-scored under future parsers —
  EXCEPT for sensitive document types (see the privacy rule).
* PRIVACY RULE: for sensitive fields (bank-check amounts and
  account/routing numbers; any field whose name mentions account,
  routing, ssn, or card), the record stores the field NAME, the
  agreement outcome, and the image hash ONLY — never the extracted
  value, and never raw read text that contains it. Sealed/refused
  entries for sensitive fields carry "redacted": true and no value.
* The model PAIR is a first-class citizen: provider, family, and
  the exact requested and API-reported model ids for both readers,
  plus the pair's distance class. Agreement statistics are
  meaningless without knowing how independent the pair was.
* A refusal is data, not an absence of data: every refused field or
  item carries a reason code from the taxonomy below, so refusal
  cost can be measured per class.

Record shape (version 1.1)::

    {
      "schema_version": "1.1",
      "tool": {"name": "pairseal", "version": "0.1.0"},
      "run_id": "<uuid4>",
      "recorded_at": "<ISO-8601 UTC>",
      "doc_type": "flyer" | "invoice" | "receipt" | "check"
                  | "generic",
      "doc_type_source": "user" | "auto" | "auto_fallback_generic",
      "image": {"sha256": ..., "bytes": int, "media_type": "image/png"},
      "source": {"url": str|null, "retailer": str|null,
                 "posted_at": str|null, "note": str|null},
      "evaluation_date": "YYYY-MM-DD",       # the "today" the window
                                             # gate judged against
      "pair": {
        "readers": [ {"provider": ..., "family": ...,
                      "model_requested": ..., "model_reported": ...},
                     {...} ],
        "same_provider": bool, "same_family": bool,
        "distance": "cross_provider" | "same_provider_cross_family"
                    | "same_family" | "single_reader"
      },
      "reads": [ {"reader": <reader identity>, "status":
                    "ok" | "unreadable" | "error",
                  "error": {"code": ..., "status": int} | null,
                  "raw_text": str|null,       # null when redacted
                  "raw_redacted": bool,
                  "parsed": {...} | null,     # values redacted for
                                              # sensitive fields
                  "usage": {...} | null} ],
      "window": {"validity_text": str|null,
                 "starts_on": "YYYY-MM-DD"|null,
                 "ends_on": "YYYY-MM-DD"|null,
                 "expired": bool} | null,     # flyer only
      "fields": [ {"name": str, "value": <normalised>,
                   "redacted": bool} ],       # sealed fields
      "tuples": [ {"name": str, "price_cents": int, "price": float,
                   "unit": str|null, "qty": float|null,
                   "name_a": str, "name_b": str} ],   # sealed items
      "refusals": [ {"subject": "field"|"item"|"image",
                     "name": str|null, "reason": <code>,
                     "detail": str, "redacted": bool} ],
      "outcome": "sealed" | "refused",
      "counts": {"sealed_fields": int, "sealed_items": int,
                 "refused": int}
    }

Refusal reason codes (the taxonomy):

    models_disagree        both reads saw the item but conflict on
                           price (to the cent), unit, or quantity
    field_conflict         both reads extracted the field but the
                           normalised values conflict
    cross_field_mismatch   two sealed values inside the document
                           contradict each other (a check's numeric
                           amount vs its written amount)
    single_read_only       only one read saw the field/item
    unreadable             a read could not read the image, or its
                           reply was unusable
    no_price_found         the reads were usable but nothing
                           extractable was found in the image
    ambiguous_price_format the printed money value cannot be
                           resolved to a unique value (e.g. a bare
                           "599": $599, $5.99 and 599 cents are all
                           live readings)
    expired_window         the agreed price sits inside a printed
                           validity window that has already ended
    provider_error         a provider call failed (HTTP status kept);
                           a 429 is a hard stop for that provider in
                           that run and is never retried in a loop
    image_unavailable      the image bytes could not be obtained at
                           all (pipeline-level; never produced by the
                           gate itself, reserved for collectors)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

SCHEMA_VERSION = "1.1"
TOOL_NAME = "pairseal"

REFUSAL_REASONS = (
    "models_disagree",
    "field_conflict",
    "cross_field_mismatch",
    "single_read_only",
    "unreadable",
    "no_price_found",
    "ambiguous_price_format",
    "expired_window",
    "provider_error",
    "image_unavailable",
)


def new_run_id() -> str:
    return str(uuid.uuid4())


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def pair_block(identities: list[dict]) -> dict:
    """Build the pair metadata block from reader identities.

    An identity is {"provider", "family", "model_requested",
    "model_reported"}. Distance is the honest summary of how
    independent the two reads can claim to be."""
    readers = [
        {
            "provider": i.get("provider"),
            "family": i.get("family"),
            "model_requested": i.get("model_requested"),
            "model_reported": i.get("model_reported"),
        }
        for i in identities
    ]
    if len(readers) < 2:
        return {"readers": readers, "same_provider": None,
                "same_family": None, "distance": "single_reader"}
    a, b = readers[0], readers[1]
    same_provider = a["provider"] == b["provider"]
    same_family = a["family"] == b["family"]
    if not same_provider:
        distance = "cross_provider"
    elif not same_family:
        distance = "same_provider_cross_family"
    else:
        distance = "same_family"
    return {"readers": readers, "same_provider": same_provider,
            "same_family": same_family, "distance": distance}


def validate_record(record: dict) -> list[str]:
    """Minimal structural validation of a corpus record. Returns a
    list of problems (empty when the record is well-formed). This is
    a shape check, not a truth check."""
    problems = []
    if not isinstance(record, dict):
        return ["record is not an object"]
    if record.get("schema_version") != SCHEMA_VERSION:
        problems.append("schema_version missing or not "
                        + SCHEMA_VERSION)
    for key in ("run_id", "recorded_at", "image", "pair", "reads",
                "tuples", "refusals", "outcome"):
        if key not in record:
            problems.append(f"missing key: {key}")
    image = record.get("image") or {}
    if not image.get("sha256"):
        problems.append("image.sha256 missing")
    for refusal in record.get("refusals") or []:
        if refusal.get("reason") not in REFUSAL_REASONS:
            problems.append(
                f"unknown refusal reason: {refusal.get('reason')!r}")
    for tup in record.get("tuples") or []:
        cents = tup.get("price_cents")
        if not isinstance(cents, int) or cents <= 0:
            problems.append(f"tuple with bad price_cents: {tup!r}")
    if record.get("outcome") not in ("sealed", "refused"):
        problems.append(f"bad outcome: {record.get('outcome')!r}")
    return problems
