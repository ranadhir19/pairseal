# Compliance record — pairseal

Originality and IP record for this repository, kept in-tree per
the portfolio's pre-publication gate. The full ledger entry lives
in the brand goal's IP/legal compliance ledger.

## Name

`pairseal` is the publication name, chosen by the owner
2026-11-16. It names the mechanism: a pair of reads, sealed on
agreement. Collision checks run 2026-11-15 (GitHub repository
search `<name> in:name`, PyPI project JSON):

- `pairseal`: GitHub 0 repositories, PyPI not registered.
- Also checked and passed over: `sealpair` (GitHub 0, PyPI
  free), `twinseal` (GitHub 1, PyPI free).
- Rejected: `fieldseal` (GitHub 9, PyPI taken), `docseal`
  (GitHub 25, PyPI taken), `veriseal` (GitHub 15), `agreewise`
  (GitHub 20), `readseal` (GitHub 3), `dualproof` / `proofpair`
  (GitHub 1 each).

No third party's branding, copy, or assets are used under the
name.

## Originality

The subject area — extracting structured fields from document
images — is served by commercial APIs and open models alike; no
name, text, structure, or code was taken from any of them. The
tool's own contribution is the verification layer: a dual-read
agreement gate with a reason-coded refusal taxonomy, per-type
schemas, a check amount cross-check, a privacy-redacted corpus
format, and a fault-injection self-test that measures the gate.
All code and copy in this repository were authored for it. The
reconciliation design generalises an earlier, unpublished
in-house lane implementation by the same author (the Waypoint
Facebook sourcing lane); no third-party code is reproduced.

## Copy

README, docs, and this record were authored for this repository.
Measured outputs quoted in the README are the tool's own real
outputs on its bundled synthetic fixtures and on a locally
rendered control flyer.

## Licence

MIT at the repo root (Copyright (c) 2026 Ranadhir Ghosh),
consistent with `pyproject.toml`.

## Dependencies

Core package: **none** — Python standard library only (verified
by a clean-venv `pip install .` + console-script run). The
optional `selftest` extra pulls Pillow (>= 10, HPND-licensed)
solely to render synthetic fixtures. Provider APIs are called
over HTTPS; no provider SDK is used or vendored.

## Vendored assets

None. No third-party code, images, fonts, or data. All fixtures
are synthetic documents rendered in-tree at self-test time from
fictional businesses and people ("Acme", "Jordan Miller"); the
check fixture's routing number is the publicly printed example
value used throughout banking documentation and its account
number is sequential and fictional — both exist only as fixture
text inside generated images, and the self-test asserts they
never reach a corpus record.

## Secrets / PII

No credentials are read from files, arguments, or source, and
none are printed: keys resolve from the Muse connector
credential store or from standard provider environment variables
at runtime. The harness's secrets sweep (validate.py stage 4)
scans the exact tree for key-shaped strings and credential
surrogates; it passes. By design, corpus records for sensitive
document types store field names, agreement outcomes, and image
hashes only — never extracted values.
