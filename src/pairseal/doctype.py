"""Per-document-type extraction schemas.

The agreement gate is document-agnostic; what varies by document
type is WHICH fields and items a read must extract and how each is
compared. This module is the single source of those definitions.

Field kinds and their comparison rules (in pairseal.reads):
    money   resolved to integer cents; a printed format that cannot
            be resolved uniquely is ambiguous, never guessed
    date    parsed to an ISO calendar date
    number  compared as floats (quantities)
    text    compared on a normalised token key

`items` are line-item lists (flyer products, invoice lines, receipt
lines): each has a name, a money value, and an optional unit/qty,
reconciled with the same agreement rules across all types.

`sensitive_fields` are the privacy rule: for those fields the
corpus record stores the field NAME and the agreement outcome only
— never the extracted value, and never the raw read text that
contains it. Bank checks are the sensitive type: amounts and
account/routing numbers never enter a corpus log. Independently of
type, any field whose name mentions account/routing/ssn/card is
treated as sensitive (see is_sensitive).
"""

from __future__ import annotations

import re

FIELD_KINDS = ("money", "date", "number", "text")

DOCTYPES: dict[str, dict] = {
    "flyer": {
        "label": "Retail flyer / shelf label / handwritten price sign",
        "fields": {},
        "items": True,
        "item_name": "product",
        "window": True,
        "sensitive_fields": frozenset(),
        "cross_checks": (),
    },
    "invoice": {
        "label": "Invoice",
        "fields": {
            "vendor": "text",
            "invoice_number": "text",
            "invoice_date": "date",
            "tax": "money",
            "total": "money",
        },
        "items": True,
        "item_name": "line item",
        "window": False,
        "sensitive_fields": frozenset(),
        "cross_checks": (),
    },
    "receipt": {
        "label": "Receipt",
        "fields": {
            "merchant": "text",
            "receipt_date": "date",
            "total": "money",
        },
        "items": True,
        "item_name": "item",
        "window": False,
        "sensitive_fields": frozenset(),
        "cross_checks": (),
    },
    "check": {
        "label": "Bank check",
        "fields": {
            "payee": "text",
            "check_date": "date",
            "amount_numeric": "money",
            "amount_written": "written_amount",
            "routing_number": "text",
            "account_number": "text",
            "memo": "text",
        },
        "items": False,
        "item_name": "item",
        "window": False,
        "sensitive_fields": frozenset({
            "amount_numeric", "amount_written",
            "routing_number", "account_number",
        }),
        "cross_checks": ("amount_words_match",),
    },
    "generic": {
        "label": "Generic document (user-named fields)",
        "fields": {},  # supplied at runtime via generic_fields
        "items": False,
        "item_name": "item",
        "window": False,
        "sensitive_fields": frozenset(),
        "cross_checks": (),
    },
}

_SENSITIVE_NAME = re.compile(r"account|routing|ssn|card|iban",
                             re.IGNORECASE)


def is_sensitive(doc_type: str, field: str) -> bool:
    schema = DOCTYPES.get(doc_type) or {}
    if field in (schema.get("sensitive_fields") or frozenset()):
        return True
    return bool(_SENSITIVE_NAME.search(field))


def schema_for(doc_type: str,
               generic_fields: list[str] | None = None) -> dict:
    """The effective schema for a run. For `generic`, the field set
    is the user's list (all text kind unless a name ends in
    `_amount`/`_total`/`_price` -> money, or `_date` -> date)."""
    base = DOCTYPES[doc_type]
    if doc_type != "generic":
        return base
    fields = {}
    for name in generic_fields or []:
        if name.endswith(("_amount", "_total", "_price", "_tax")):
            fields[name] = "money"
        elif name.endswith("_date"):
            fields[name] = "date"
        else:
            fields[name] = "text"
    return {**base, "fields": fields}
