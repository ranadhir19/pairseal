# Examples

- `sample_record.json` — one real corpus record (schema 1.1)
  produced by the fault-injection self-test's `invoice_clean`
  fixture: a synthetic invoice rendered locally, read by two
  scripted readers, judged by the real gate. All five fields and
  both line items sealed; the pair distance is `same_family`
  because scripted readers share a fictional provider — live runs
  record the real pair.

To reproduce the full fixture set and its verdicts:

```bash
pip install .[selftest]
pairseal selftest
```
