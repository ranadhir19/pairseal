# pairseal self-test results

Fault-injection self-test: synthetic documents with known
ground truth, read by scripted readers, judged by the real
gate. It measures the gate — parsing, normalisation,
reconciliation, expiry, cross-checks, redaction — offline
and deterministically. Live model accuracy is a property
of the models and is measured by real corpus runs, not
here.

Evaluation date for all fixtures: 2026-12-02.

| Fixture | Type | Kind | Sealed fields | Sealed items | Refused | Result |
|---|---|---|---|---|---|---|
| flyer_clean | flyer | fixture | 0 | 4 | 0 | PASS |
| flyer_traps | flyer | fixture | 0 | 1 | 3 | PASS |
| flyer_expired | flyer | fixture | 0 | 0 | 1 | PASS |
| flyer_unreadable | flyer | fixture | 0 | 0 | 1 | PASS |
| invoice_clean | invoice | fixture | 5 | 2 | 0 | PASS |
| invoice_conflict | invoice | fixture | 2 | 1 | 1 | PASS |
| check_clean | check | fixture | 7 | 0 | 0 | PASS |
| check_mismatch | check | fixture | 5 | 0 | 2 | PASS |
| receipt_clean | receipt | fixture | 3 | 2 | 0 | PASS |
| generic_clean | generic | fixture | 2 | 0 | 0 | PASS |
| empty_reads | flyer | control | 0 | 0 | 1 | PASS |
| provider_429 | flyer | control | 0 | 0 | 1 | PASS |

Fixtures: 10/10 passed. Controls: 2/2 passed.

Global invariant held across every fixture: no sealed field or item
carries a value that differs from the ground truth rendered in its
image. The gate may refuse a truth; it never invents one.
