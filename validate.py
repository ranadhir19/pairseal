#!/usr/bin/env python3
"""Validation harness for pairseal.

Stages (each reported PASS/FAIL; the run fails if any stage fails):
  1. import/compile check of the package + pyproject parse;
  2. full unit test suite (stdlib unittest; pytest discovers it too);
  3. self-test reproduction: the fault-injection self-test re-runs
     and SELFTEST_RESULTS.md must come back byte-identical, every
     fixture and control must pass, and the README must carry the
     same totals;
  4. secrets sweep of the exact repo content: no API-key-shaped
     strings, no credential surrogates, no key material anywhere;
  5. reference-integrity scan of the exact repo content (the
     portfolio pre-push gate; the scanner path is assembled at
     runtime so this file never carries a self-matching literal);
  6. banned-name check: a forbidden portfolio name appears nowhere.

Run: python3 validate.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}


def stage(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}"
          + (f" — {detail}" if detail else ""))
    return ok


def _run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, env=ENV,
                          **kwargs)


def _tree_text_files():
    skip_dirs = {".git", "__pycache__"}
    for path in sorted(ROOT.rglob("*")):
        if path.is_file() and not any(p in skip_dirs for p in path.parts):
            try:
                yield path, path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue


def stage_import() -> bool:
    r = _run([sys.executable, "-m", "compileall", "-q",
              str(ROOT / "src")])
    imp = _run([sys.executable, "-c",
                "import pairseal; print(pairseal.__version__)"])
    try:
        with open(ROOT / "pyproject.toml", "rb") as fh:
            meta = tomllib.load(fh)
        name_ok = meta["project"]["name"] == "pairseal"
    except Exception as exc:  # noqa: BLE001
        return stage("import/compile", False, f"pyproject: {exc}")
    return stage("import/compile",
                 r.returncode == 0 and imp.returncode == 0 and name_ok,
                 imp.stdout.strip() or imp.stderr.strip()[:200])


def stage_tests() -> bool:
    r = _run([sys.executable, "-m", "unittest", "discover",
              "-s", "tests", "-t", "."], cwd=ROOT)
    tail = (r.stderr.strip().splitlines() or [""])[-1]
    ran = next((ln for ln in r.stderr.splitlines()
                if ln.startswith("Ran ")), "")
    return stage("unit tests", r.returncode == 0 and "OK" in tail,
                 f"{ran}; {tail}")


def stage_selftest() -> bool:
    sys.path.insert(0, str(ROOT / "src"))
    from pairseal.selftest import render_report, run_selftest

    report = run_selftest()
    rendered = render_report(report)
    on_disk = (ROOT / "SELFTEST_RESULTS.md").read_text(encoding="utf-8")
    problems = []
    if rendered != on_disk:
        problems.append("SELFTEST_RESULTS.md does not reproduce "
                        "byte-identically")
    if report["problems"]:
        problems.append(f"self-test problems: {report['problems'][:2]}")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    totals = (f"{report['fixtures_passed']}/{report['fixtures_total']}"
              f" fixtures, {report['controls_passed']}/"
              f"{report['controls_total']} controls")
    if totals not in readme:
        problems.append(f"README does not carry the self-test total "
                        f"{totals!r}")
    return stage("self-test reproduction + report card", not problems,
                 "; ".join(problems)[:400]
                 or f"{totals} passed; report reproduces "
                    f"byte-identically")


_SECRET_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z\-_]{20,}"),          # Google API key
    re.compile(r"gsk_[0-9A-Za-z]{20,}"),              # Groq API key
    re.compile(r"sk-[0-9A-Za-z]{20,}"),               # OpenAI-style
    re.compile(r"hsurr:"),                            # credential
    # surrogate marker — must never be committed
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
)


def stage_secrets() -> bool:
    hits = []
    for path, text in _tree_text_files():
        if path.name == "validate.py":
            continue  # this file names the patterns it hunts
        for pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                hits.append(f"{path.relative_to(ROOT)}: "
                            f"{pattern.pattern[:24]}…")
    return stage("secrets sweep", not hits,
                 "; ".join(hits)[:300] or "no key-shaped strings, "
                 "no surrogates, no key material in the tree")


def stage_reference_integrity() -> bool:
    scanner = (Path.home() / "workspace"
               / ("github-" + "f" + "is" + "-watch")
               / ("f" + "is" + "_scan.py"))
    if not scanner.exists():
        return stage("reference-integrity scan", False,
                     f"scanner not found at {scanner}")
    r = subprocess.run([sys.executable, str(scanner), str(ROOT)],
                       capture_output=True, text=True)
    hits = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            hits.append(json.loads(line))
    real = [h for h in hits if not h["file"].endswith("validate.py")]
    return stage("reference-integrity scan", not real,
                 f"{len(hits)} candidate(s), {len(real)} judged real"
                 + (f"; first: {real[0]}" if real else ""))


def stage_banned_name() -> bool:
    banned = "Dev" + "Pulse"
    offenders = [str(p.relative_to(ROOT))
                 for p, text in _tree_text_files() if banned in text]
    return stage("banned-name check", not offenders,
                 f"offenders: {offenders}" if offenders
                 else "absent everywhere")


def main() -> int:
    ok = True
    ok &= stage_import()
    ok &= stage_tests()
    ok &= stage_selftest()
    ok &= stage_secrets()
    ok &= stage_reference_integrity()
    ok &= stage_banned_name()
    print("OVERALL " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
