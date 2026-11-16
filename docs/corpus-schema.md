# Corpus record schema — version 1.1

Every pairseal run emits exactly one record in this shape, as a
JSON object. `pairseal extract --corpus PATH` appends records to
a JSONL log; `pairseal verify-corpus PATH` validates and
summarises a log. The normative definition lives in
`src/pairseal/schema.py`; this document is the human-readable
companion.

## Design rules

1. **The image is never stored.** Only its SHA-256, byte count,
   and media type. The image stays with its source.
2. **Reads are stored raw and parsed** — the model's unparsed
   reply alongside its normalised parse — so a corpus can be
   re-scored under future parsers. Exception: the privacy rule.
3. **Privacy rule.** For sensitive fields (bank-check amounts and
   account/routing numbers; any field whose name mentions
   account, routing, ssn, or card), the record stores the field
   name, the agreement outcome, and the image hash ONLY — never
   the extracted value, and never raw read text that contains it.
   Affected entries carry `"redacted": true`; the reads block
   carries `"raw_redacted": true` and `raw_text: null`, and
   sensitive parsed values are nulled.
4. **The model pair is first-class.** Provider, family, requested
   and API-reported model ids for both readers, plus the pair
   distance class — agreement statistics are meaningless without
   knowing how independent the pair actually was.
5. **A refusal is data.** Every refused field/item carries a
   reason code from the taxonomy, so refusal cost can be measured
   per class.

## Fields

| Key | Type | Meaning |
|---|---|---|
| `schema_version` | string | `"1.1"` |
| `tool` | object | `{"name": "pairseal", "version": ...}` |
| `run_id` | string (uuid4) | one per run |
| `recorded_at` | string (ISO-8601 UTC) | when the run happened |
| `doc_type` | string | `flyer` \| `invoice` \| `receipt` \| `check` \| `generic` |
| `doc_type_source` | string | `user` \| `auto` \| `auto_fallback_generic` |
| `image` | object | `{"sha256", "bytes", "media_type"}` |
| `source` | object | `{"url", "retailer", "posted_at", "note"}` — provenance as supplied by the caller; the tool does not invent it |
| `evaluation_date` | string (date) | the "today" the validity-window gate judged against |
| `pair` | object | `{"readers": [identity, identity], "same_provider", "same_family", "distance"}` where distance ∈ `cross_provider` \| `same_provider_cross_family` \| `same_family` \| `single_reader` |
| `reads` | array | per reader: `{"reader": identity, "status": "ok"\|"unreadable"\|"error", "error": {"code", "status"} \| null, "raw_text", "raw_redacted", "parsed", "usage"}` |
| `window` | object \| null | flyer only: `{"validity_text", "starts_on", "ends_on", "expired", "conflict"}` |
| `fields` | array | sealed fields: `{"name", "value"?, "redacted"}` — `value` (normalised: money in cents, dates ISO, text as read) is absent when redacted |
| `tuples` | array | sealed items: `{"name", "price_cents", "price", "unit", "qty", "name_a", "name_b"}` — both reads' names kept for audit |
| `refusals` | array | `{"subject": "field"\|"item"\|"image", "name", "reason", "detail", "redacted", "value_a"?, "value_b"?}` — values omitted when redacted |
| `outcome` | string | `sealed` when at least one field or item sealed, else `refused` |
| `counts` | object | `{"sealed_fields", "sealed_items", "refused"}` |

## Refusal taxonomy

`models_disagree` (item price/unit/qty conflict) ·
`field_conflict` (field value conflict) ·
`cross_field_mismatch` (sealed values contradict inside the
document, e.g. check amounts) · `single_read_only` ·
`unreadable` · `no_price_found` · `ambiguous_price_format` ·
`expired_window` · `provider_error` · `image_unavailable`
(collector-level; the gate never emits it).

## Versioning

Schema changes that add optional keys keep the 1.x line and bump
the minor version; changes that rename or remove keys bump the
major version. `validate_record` in `pairseal.schema` enforces
the current version's shape.
